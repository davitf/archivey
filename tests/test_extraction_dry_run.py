"""``dry_run=True``: the same extraction, into a scratch directory, with no content kept.

The contract is parity: a dry run reports what a real extraction into an empty
destination reports, member for member, and leaves nothing behind. Most tests here
run both and compare, over the declarative corpus, the adversarial name corpus, and
the symlink-redirection cases that only a real filesystem answers (threat model O22).
"""

from __future__ import annotations

import gzip
import io
import logging
import os
import re
import tarfile
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

import archivey
from archivey import (
    AbortOn,
    ExtractionError,
    ExtractionLimits,
    ExtractionPolicy,
    ExtractionStatus,
    NameCollisionError,
    OnError,
    OverwritePolicy,
    ResourceLimitError,
    open_archive,
)
from archivey.cli.exit_codes import EXIT_OK, EXIT_POLICY
from archivey.cli.main import main
from archivey.terminal import display_path
from tests.create_adversarial import adversarial_archives
from tests.sample_archives import CORPUS, corpus_archive_path, skip_unless_runnable

_POLICIES = list(ExtractionPolicy)
_TMP_NAME = re.compile(r"\.archivey-tmp-[^'\"/]+")
_SINGLE_FILE_KEYS = {"gz", "gz-meta", "bz2", "xz", "zst", "lz4", "lz", "zz", "br"}


@pytest.fixture(autouse=True)
def _private_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``tempfile`` at a directory of this test's own, to see what is left."""
    scratch_parent = tmp_path / "tmp"
    scratch_parent.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch_parent))
    return scratch_parent


def _assert_nothing_left(tmp_path: Path, dest: Path) -> None:
    assert not os.path.lexists(dest), "a dry run created its destination"
    assert list((tmp_path / "tmp").iterdir()) == [], "scratch directory left behind"


def _rel(path: Path | None, dest: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(dest).as_posix()
    except ValueError:  # spelled unlike dest: shown as is, to fail the comparison
        return f"elsewhere: {path.as_posix()}"


def _normalize(text: str, dest: Path, cwd: Path) -> str:
    """``text`` with dest factored out, keeping apart how it was spelled.

    Each spelling gets its own label: ``<given>`` for an absolute dest as given,
    ``<abs>`` for ``os.path.abspath`` of it, ``<resolved>`` for its resolution and
    ``<rel>`` for a relative dest as given. A run that reports ``out/x`` where the other
    reports ``/tmp/.../out/x``, or a symlink's target where the other names the
    symlink, does not compare equal.
    """
    absolute = os.path.abspath(cwd / dest)
    try:
        resolved = str((cwd / dest).resolve())
    except (OSError, RuntimeError):  # a symlink loop on the way
        resolved = absolute
    labels = {resolved: "<resolved>", absolute: "<abs>"}
    if dest.is_absolute():
        labels.setdefault(str(dest), "<given>")
    for spelling in sorted(labels, key=len, reverse=True):
        text = text.replace(spelling, labels[spelling])
    text = text.replace(str(cwd.resolve()), "<cwd>")
    if not dest.is_absolute():
        text = re.sub(rf"(?<=['\"]){re.escape(str(dest))}(?=['\"/\\])", "<rel>", text)
    # The staging file's random suffix is the one thing two runs never share.
    return _TMP_NAME.sub(".archivey-tmp-*", text)


def _shape(results, dest: Path, cwd: Path) -> list[tuple[object, ...]]:
    """What a caller can observe of a report, with the destination factored out."""
    shape = []
    for r in results:
        error = r.error
        message = None if error is None else _normalize(str(error), dest, cwd)
        shape.append(
            (
                r.member.name,
                r.status,
                _rel(r.path, dest),
                _rel(r.requested_path, dest),
                _rel(r.collided_with, dest),
                r.presented_name,
                r.failure_group_size,
                type(error),
                message,
            )
        )
    return shape


def _both(
    source,
    tmp_path: Path,
    *,
    relative: bool = False,
    dest_name: str = "out",
    **kwargs,
):
    """Extract ``source`` for real and as a dry run; return both report shapes.

    Each run extracts into ``<tmp_path>/<real|dry>/<dest_name>``. With ``relative``,
    it is given ``dest_name`` relative to the current directory, as the CLI gives it.
    """
    kwargs.setdefault("on_error", OnError.CONTINUE)
    open_kwargs = kwargs.pop("open_kwargs", {})
    outcomes = []
    for kind, dry_run in (("real", False), ("dry", True)):
        cwd = tmp_path / kind
        cwd.mkdir(exist_ok=True)
        dest = Path(dest_name) if relative else cwd / dest_name
        previous = os.getcwd()
        os.chdir(cwd)
        try:
            with open_archive(source(), **open_kwargs) as reader:
                try:
                    report = reader.extract_all(dest, dry_run=dry_run, **kwargs)
                except (archivey.ArchiveyError, OSError) as exc:
                    outcomes.append(
                        ("raised", type(exc), _normalize(str(exc), dest, cwd))
                    )
                else:
                    outcomes.append(_shape(report.results, dest, cwd))
        finally:
            os.chdir(previous)
    _assert_nothing_left(tmp_path, tmp_path / "dry" / dest_name)
    return outcomes[0], outcomes[1]


# --- parity over the corpora --------------------------------------------------------

_CORPUS_PARAMS = [
    pytest.param(entry, key, id=f"{entry.id}-{key}")
    for entry in CORPUS
    for key in entry.formats
    if key not in _SINGLE_FILE_KEYS
]


@pytest.mark.parametrize(("entry", "key"), _CORPUS_PARAMS)
def test_corpus_dry_run_matches_real_extraction(entry, key, tmp_path: Path) -> None:
    skip_unless_runnable(entry, key)
    path = corpus_archive_path(entry, key, tmp_path)
    passwords = list(entry.passwords) or None
    for policy in _POLICIES:
        work = tmp_path / policy.value
        (work / "tmp").mkdir(parents=True)
        tempfile.tempdir = str(work / "tmp")
        real, dry = _both(
            lambda: path,
            work,
            policy=policy,
            open_kwargs={"password": passwords},
        )
        assert dry == real, policy


_ADVERSARIAL = adversarial_archives()


@pytest.mark.parametrize(
    ("entry", "blob"), _ADVERSARIAL, ids=[entry.id for entry, _ in _ADVERSARIAL]
)
def test_adversarial_dry_run_matches_real_extraction(
    entry, blob: bytes, tmp_path: Path
) -> None:
    if entry.open_outcome == "corruption":
        pytest.skip("does not open")
    for policy in _POLICIES:
        work = tmp_path / policy.value
        (work / "tmp").mkdir(parents=True)
        tempfile.tempdir = str(work / "tmp")
        real, dry = _both(lambda: io.BytesIO(blob), work, policy=policy)
        assert dry == real, policy


# --- the checks only a filesystem answers -------------------------------------------


def _tar(entries: list[tuple[str, str, object]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, kind, extra in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                data = extra if isinstance(extra, bytes) else b"data"
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = extra if isinstance(extra, int) else 0o755
                tf.addfile(info)
            elif kind == "sym":
                info.type = tarfile.SYMTYPE
                info.linkname = str(extra)
                tf.addfile(info)
            elif kind == "hard":
                info.type = tarfile.LNKTYPE
                info.linkname = str(extra)
                tf.addfile(info)
    return buf.getvalue()


_FILESYSTEM_CASES = {
    # A file written through a symlinked parent the archive made itself.
    "through-own-symlink": [("d", "dir", None), ("l", "sym", "d"), ("l/f", "file", 0)],
    # O22: a link that a later member makes escape.
    "made-escaping-later": [
        ("l", "sym", "a/../../secret"),
        ("a", "sym", "."),
    ],
    # A chain of links that ends outside.
    "escaping-chain": [
        ("x", "sym", "y"),
        ("y", "sym", ".."),
        ("x/f", "file", 0),
    ],
    # A hardlink to a file, and one whose source the archive never wrote.
    "hardlinks": [
        ("f", "file", b"content"),
        ("h", "hard", "f"),
        ("orphan", "hard", "missing"),
    ],
    # Duplicate names and a file where a directory was.
    "duplicates": [
        ("a", "file", b"one"),
        ("a", "file", b"two"),
        ("d", "file", 0),
        ("d/f", "file", 0),
    ],
}


@pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")
@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
@pytest.mark.parametrize("case", sorted(_FILESYSTEM_CASES))
@pytest.mark.parametrize(
    "overwrite",
    [OverwritePolicy.ERROR, OverwritePolicy.RENAME, OverwritePolicy.REPLACE],
)
@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_filesystem_dependent_outcomes_match(
    case: str,
    overwrite: OverwritePolicy,
    streaming: bool,
    relative: bool,
    tmp_path: Path,
) -> None:
    blob = _tar(_FILESYSTEM_CASES[case])
    for policy in _POLICIES:
        work = tmp_path / policy.value
        (work / "tmp").mkdir(parents=True)
        tempfile.tempdir = str(work / "tmp")
        real, dry = _both(
            lambda: io.BytesIO(blob),
            work,
            relative=relative,
            policy=policy,
            overwrite=overwrite,
            open_kwargs={"streaming": streaming},
        )
        assert dry == real, policy


def _dest_named_links(dest: Path) -> list[tuple[str, str, object]]:
    """Links whose targets name ``dest`` itself, so each run needs its own archive."""
    return [
        ("data", "file", b"content"),
        # Absolute, into dest: kept by a real extraction.
        ("abs", "sym", f"{dest}/data"),
        ("abs-dotdot", "sym", f"{dest}/sub/../data"),
        # A file written through an absolute link to a directory in dest.
        ("sub", "dir", None),
        ("abs-dir", "sym", f"{dest}/sub"),
        ("abs-dir/f", "file", 0),
        # Absolute, outside dest: refused by a real extraction too.
        ("abs-out", "sym", "/archivey-dry-run-test-elsewhere"),
        # Relative, out of dest by name and back in.
        ("up", "sym", f"../{dest.name}/data"),
        # O22 on an absolute target: inside when made, outside once "a" is dest.
        ("later", "sym", f"{dest}/a/b/../../data"),
        ("a", "sym", "."),
        # A hardlink whose (archive-relative) target is absolute.
        ("hard-abs", "hard", f"{dest}/data"),
    ]


@pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")
@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
def test_link_targets_that_name_dest_match(streaming: bool, tmp_path: Path) -> None:
    for policy in _POLICIES:
        work = tmp_path / policy.value
        (work / "tmp").mkdir(parents=True)
        tempfile.tempdir = str(work / "tmp")
        blobs = {
            dry_run: _tar(_dest_named_links(work / kind / "out"))
            for dry_run, kind in ((False, "real"), (True, "dry"))
        }
        calls = iter((False, True))
        real, dry = _both(
            lambda: io.BytesIO(blobs[next(calls)]),
            work,
            policy=policy,
            open_kwargs={"streaming": streaming},
        )
        assert dry == real, policy
        statuses = {entry[0]: entry[1] for entry in dry}
        assert statuses["abs"] is ExtractionStatus.EXTRACTED
        assert statuses["up"] is ExtractionStatus.EXTRACTED
        assert statuses["abs-dir/f"] is ExtractionStatus.EXTRACTED
        assert statuses["later"] is ExtractionStatus.BLOCKED


# --- what the dry run does and does not touch ---------------------------------------


def test_bodies_are_read_and_verified(tmp_path: Path) -> None:
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("good.txt", b"fine")
        zf.writestr("bad.txt", b"payload that gets damaged")
    blob = bytearray(path.read_bytes())
    at = blob.index(b"payload that gets damaged")
    blob[at] ^= 0xFF
    path.write_bytes(bytes(blob))

    dest = tmp_path / "out"
    report = archivey.extract(path, dest, on_error=OnError.CONTINUE, dry_run=True)
    statuses = {r.member.name: r.status for r in report.results}
    assert statuses == {
        "good.txt": ExtractionStatus.EXTRACTED,
        "bad.txt": ExtractionStatus.FAILED,
    }
    _assert_nothing_left(tmp_path, dest)


def test_limits_still_apply(tmp_path: Path) -> None:
    blob = _tar([("big", "file", b"x" * 4096)])
    dest = tmp_path / "out"
    with open_archive(io.BytesIO(blob)) as reader:
        with pytest.raises(ResourceLimitError):
            reader.extract_all(
                dest, limits=ExtractionLimits(max_extracted_bytes=1024), dry_run=True
            )
    _assert_nothing_left(tmp_path, dest)


def test_existing_destination_is_neither_read_nor_changed(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "a").write_bytes(b"mine")
    blob = _tar([("a", "file", b"theirs")])
    with open_archive(io.BytesIO(blob)) as reader:
        report = reader.extract_all(dest, dry_run=True)
    # The run shows an extraction into an empty destination: no collision with "a".
    (result,) = report.results
    assert result.status is ExtractionStatus.EXTRACTED
    assert result.path == dest / "a"
    assert (dest / "a").read_bytes() == b"mine"
    assert sorted(p.name for p in dest.iterdir()) == ["a"]


def test_destination_that_is_a_file_is_refused(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.write_bytes(b"")
    with open_archive(io.BytesIO(_tar([("a", "file", 0)]))) as reader:
        with pytest.raises(ExtractionError, match="not a directory"):
            reader.extract_all(dest, dry_run=True)
    assert list((tmp_path / "tmp").iterdir()) == []


def test_destination_under_a_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "afile").write_bytes(b"")
    blob = _tar([("a", "file", 0)])
    raised = []
    for dry_run in (False, True):
        dest = tmp_path / "afile" / ("dry" if dry_run else "real") / "out"
        with open_archive(io.BytesIO(blob)) as reader:
            with pytest.raises(OSError) as caught:
                reader.extract_all(dest, dry_run=dry_run)
        raised.append(caught.value)
    real, dry = raised
    if os.name != "nt":
        assert type(dry) is type(real)
    assert "archivey-dry-run-" not in str(dry)
    assert list((tmp_path / "tmp").iterdir()) == []


_NON_ROOT = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes anywhere"
)


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes and symlinks")
@_NON_ROOT
@pytest.mark.parametrize(
    "shape", ["unwritable-parent", "unwritable-grandparent", "symlink-loop"]
)
@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_destination_that_cannot_be_created_is_refused_alike(
    shape: str, relative: bool, tmp_path: Path
) -> None:
    for kind in ("real", "dry"):
        cwd = tmp_path / kind
        cwd.mkdir()
        if shape == "symlink-loop":
            (cwd / "base").symlink_to("base")
        else:
            (cwd / "base").mkdir(mode=0o555)
    tail = "a/out" if shape == "unwritable-grandparent" else "out"
    try:
        real, dry = _both(
            lambda: io.BytesIO(_tar([("data", "file", 0)])),
            tmp_path,
            relative=relative,
            dest_name=f"base/{tail}",
        )
    finally:
        for kind in ("real", "dry"):
            if not (tmp_path / kind / "base").is_symlink():
                (tmp_path / kind / "base").chmod(0o755)
    assert real[0] == "raised"
    assert dry == real
    assert not os.path.lexists(tmp_path / "dry" / "base" / tail)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory modes")
@_NON_ROOT
@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
@pytest.mark.parametrize(
    "dest_name", ["out", "link/out", "x/../out"], ids=["plain", "symlink", "dotdot"]
)
def test_member_errors_match_with_either_dest_spelling(
    relative: bool, dest_name: str, tmp_path: Path
) -> None:
    for kind in ("real", "dry"):
        cwd = tmp_path / kind
        (cwd / "realdir").mkdir(parents=True)
        (cwd / "link").symlink_to("realdir")
        (cwd / "x").mkdir()
    # The directory is locked before its file is written, so the write fails.
    blob = _tar([("ro", "dir", 0o555), ("ro/f", "file", 0)])
    real, dry = _both(
        lambda: io.BytesIO(blob),
        tmp_path,
        relative=relative,
        dest_name=dest_name,
        policy=ExtractionPolicy.TRUSTED,
    )
    (tmp_path / "real" / dest_name / "ro").chmod(0o755)
    assert real[-1][1] is ExtractionStatus.FAILED
    assert dry == real


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory modes")
@_NON_ROOT
def test_errors_and_warnings_name_dest(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The directory is locked before its file is written, so the write fails.
    blob = _tar([("ro", "dir", 0o555), ("ro/f", "file", 0)])
    dest = tmp_path / "out"
    with caplog.at_level(logging.WARNING, logger="archivey"):
        with open_archive(io.BytesIO(blob)) as reader:
            report = reader.extract_all(
                dest,
                policy=ExtractionPolicy.TRUSTED,
                on_error=OnError.CONTINUE,
                dry_run=True,
            )
    failed = report.results[-1]
    assert failed.status is ExtractionStatus.FAILED
    assert isinstance(failed.error, PermissionError)
    assert str(dest / "ro") in str(failed.error)
    assert "archivey-dry-run-" not in str(failed.error)
    assert str(dest / "ro") in caplog.text
    assert "archivey-dry-run-" not in caplog.text

    with open_archive(io.BytesIO(blob)) as reader:
        with pytest.raises(PermissionError) as caught:
            reader.extract_all(
                dest,
                policy=ExtractionPolicy.TRUSTED,
                on_error=OnError.STOP,
                dry_run=True,
            )
    assert str(dest / "ro") in str(caught.value)
    assert "archivey-dry-run-" not in str(caught.value)
    _assert_nothing_left(tmp_path, dest)


def test_scratch_is_removed_when_the_archive_locks_its_own_directories(
    tmp_path: Path,
) -> None:
    # The file comes first: as a non-root user, nothing can be written into a
    # directory once it is 0o555, in a dry run or a real one.
    blob = _tar([("ro/f", "file", 0), ("ro", "dir", 0o555), ("zero", "dir", 0)])
    dest = tmp_path / "out"
    with open_archive(io.BytesIO(blob)) as reader:
        report = reader.extract_all(dest, policy=ExtractionPolicy.TRUSTED, dry_run=True)
    assert {r.status for r in report.results} == {ExtractionStatus.EXTRACTED}
    _assert_nothing_left(tmp_path, dest)


def test_scratch_is_removed_on_abort_and_the_message_names_dest(
    tmp_path: Path,
) -> None:
    blob = _tar([("A", "file", 0), ("a", "file", 0)])
    dest = tmp_path / "out"
    with open_archive(io.BytesIO(blob)) as reader:
        with pytest.raises(NameCollisionError) as caught:
            reader.extract_all(dest, abort_on=[AbortOn.NAME_COLLISION], dry_run=True)
    assert display_path(dest / "A") in str(caught.value)
    assert "archivey-dry-run-" not in str(caught.value)
    _assert_nothing_left(tmp_path, dest)


# --- CLI ---------------------------------------------------------------------------


def _cli(argv: list[str]) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, out=out, err=err)
    return code, err.getvalue()


def test_cli_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "bundle.tar"
    archive.write_bytes(_tar([("a", "file", b"x"), ("../up", "file", 0)]))
    monkeypatch.chdir(tmp_path)
    code, err = _cli(["x", "bundle.tar", "--dry-run", "--hide-progress"])
    assert code == EXIT_POLICY
    assert "would extract into bundle/" in err
    assert "blocked: ../up" in err
    assert "dry run, nothing written: 1 extracted" in err
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bundle.tar", "tmp"]
    assert list((tmp_path / "tmp").iterdir()) == []


_SAME_AS_REAL = "same as the real run"  # the hoist line the real run printed, predicted

_TOO_LONG = "x" * 300  # longer than any filesystem's name limit: fails at write time


def _files(*names: str) -> list[tuple[str, str, object]]:
    return [(name, "file", b"x") for name in names]


@pytest.mark.parametrize(
    ("entries", "existing", "expected"),
    [
        (_files("src/a", "src/b"), None, "would move to src/\n"),
        (
            _files("bundle/a", "bundle/b"),
            None,
            "would remove wrapper; content at bundle/\n",
        ),
        (_files("src/a", "src/b"), "src", "would move to src/, which exists already"),
        # The root exists only because a member under it was given a directory.
        (_files(f"src/{_TOO_LONG}"), None, "would move to src/\n"),
        # The failed member's directory would be the file an earlier member wrote.
        (_files("a", "a/f"), None, "would move to a\n"),
        # The failure is creating the root itself: nothing is left to move.
        (_files(f"{_TOO_LONG}/f"), None, None),
        # ...so the one root a real run moves is the other one.
        (_files("good/f", f"{_TOO_LONG}/f"), None, "would move to good/\n"),
        # A link to itself. Before Python 3.13 the loop is refused as an escape and the
        # directory made for it is left, which is moved. From 3.13 the link is
        # created, and the hoist keeps the entry: its walk of a loop runs out of hops.
        # Not skipped on Windows on purpose: without the symlink privilege the link
        # fails to be created instead. Whichever happens, the dry run has to say what
        # the real one did.
        ([("root/a", "sym", "a")], None, _SAME_AS_REAL),
    ],
    ids=[
        "moved",
        "flattened",
        "onto-existing",
        "only-a-failed-member",
        "failed-under-a-file",
        "failed-creating-the-root",
        "beside-a-root-that-failed",
        "blocked-after-its-directory",
    ],
)
def test_cli_dry_run_names_where_a_single_root_lands(
    entries: list[tuple[str, str, object]],
    existing: str | None,
    expected: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blob = _tar(entries)
    runs = {}
    for dry_run in (False, True):
        cwd = tmp_path / ("dry" if dry_run else "real")
        cwd.mkdir()
        (cwd / "bundle.tar").write_bytes(blob)
        if existing is not None:
            (cwd / existing).mkdir()
        monkeypatch.chdir(cwd)
        argv = ["x", "bundle.tar", "--hide-progress"]
        runs[dry_run] = _cli([*argv, "--dry-run"] if dry_run else argv)
    (real_code, real_err), (dry_code, dry_err) = runs[False], runs[True]
    if expected is None:
        assert "would move" not in dry_err
        assert "would remove wrapper" not in dry_err
    elif expected is _SAME_AS_REAL:
        predicted = [
            f"would move to {line[len('moved to ') :]}"
            if line.startswith("moved to ")
            else f"would keep in {line[len('kept in ') :]}"
            for line in real_err.splitlines()
            if line.startswith(("moved to ", "kept in "))
        ]
        assert len(predicted) == 1, real_err
        assert predicted[0] in dry_err.splitlines()
    else:
        assert expected in dry_err
    assert dry_code == real_code
    # The summary names where the real run put the content.
    assert (
        real_err.splitlines()[-1].split(" → ")[1]
        == (dry_err.splitlines()[-1].split(" → ")[1])
    )
    expected_left = ["bundle.tar"] + ([existing] if existing else [])
    assert sorted(p.name for p in (tmp_path / "dry").iterdir()) == expected_left
    assert list((tmp_path / "tmp").iterdir()) == []


def _zip(names: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, b"x")
    return buf.getvalue()


@pytest.mark.parametrize(
    ("archive", "make", "extra"),
    [
        ("bundle.zip", lambda: _zip(["src/a", "src/b"]), []),
        ("bundle.zip", lambda: _zip(["only.txt"]), []),
        ("data.txt.gz", lambda: gzip.compress(b"x"), []),
        ("bundle.zip", lambda: _zip(["a", "b"]), ["-d", "."]),
    ],
    ids=["single-root-dir", "single-file", "raw-stream", "two-tops-into-cwd"],
)
def test_cli_dry_run_summary_names_what_extracts_into_the_cwd(
    archive: str,
    make: Callable[[], bytes],
    extra: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Nothing is hoisted here: the content goes straight into the cwd, and the summary
    # names the single entry it lands as.
    blob = make()
    runs = {}
    for dry_run in (False, True):
        cwd = tmp_path / ("dry" if dry_run else "real")
        cwd.mkdir()
        (cwd / archive).write_bytes(blob)
        monkeypatch.chdir(cwd)
        argv = ["x", archive, "--hide-progress", *extra]
        runs[dry_run] = _cli([*argv, "--dry-run"] if dry_run else argv)
    (real_code, real_err), (dry_code, dry_err) = runs[False], runs[True]
    assert dry_code == real_code
    assert (
        real_err.splitlines()[-1].split(" → ")[1]
        == (dry_err.splitlines()[-1].split(" → ")[1])
    )
    assert [p.name for p in (tmp_path / "dry").iterdir()] == [archive]


def test_cli_dry_run_with_dest(tmp_path: Path) -> None:
    archive = tmp_path / "bundle.tar"
    archive.write_bytes(_tar([("a", "file", b"x")]))
    dest = tmp_path / "out"
    code, err = _cli(
        ["x", str(archive), "-d", str(dest), "--dry-run", "--hide-progress"]
    )
    assert code == EXIT_OK
    assert "dry run, nothing written: 1 extracted" in err
    assert not dest.exists()
