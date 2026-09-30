"""``dry_run=True``: the same extraction, into a scratch directory, with no content kept.

The contract is parity: a dry run reports what a real extraction into an empty
destination reports, member for member, and leaves nothing behind. Most tests here
run both and compare, over the declarative corpus, the adversarial name corpus, and
the symlink-redirection cases that only a real filesystem answers (threat model O22).
"""

from __future__ import annotations

import io
import os
import re
import tarfile
import tempfile
import zipfile
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
    return None if path is None else path.relative_to(dest).as_posix()


def _shape(results, dest: Path) -> list[tuple[object, ...]]:
    """What a caller can observe of a report, with the destination factored out."""
    shape = []
    for r in results:
        error = r.error
        message = None
        if error is not None:
            # The staging file's random suffix is the one thing two runs never share.
            message = _TMP_NAME.sub(
                ".archivey-tmp-*", str(error).replace(str(dest), "<dest>")
            )
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


def _both(source, tmp_path: Path, **kwargs) -> tuple[list, list]:
    """Extract ``source`` for real and as a dry run; return both report shapes."""
    real_dest = tmp_path / "real" / "out"
    dry_dest = tmp_path / "dry" / "out"
    kwargs.setdefault("on_error", OnError.CONTINUE)
    open_kwargs = kwargs.pop("open_kwargs", {})
    outcomes = []
    for dest, dry_run in ((real_dest, False), (dry_dest, True)):
        with open_archive(source(), **open_kwargs) as reader:
            try:
                report = reader.extract_all(dest, dry_run=dry_run, **kwargs)
            except archivey.ArchiveyError as exc:
                outcomes.append(
                    ("raised", type(exc), str(exc).replace(str(dest), "<dest>"))
                )
            else:
                outcomes.append(_shape(report.results, dest))
    _assert_nothing_left(tmp_path, dry_dest)
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
def test_filesystem_dependent_outcomes_match(
    case: str, overwrite: OverwritePolicy, streaming: bool, tmp_path: Path
) -> None:
    blob = _tar(_FILESYSTEM_CASES[case])
    for policy in _POLICIES:
        work = tmp_path / policy.value
        (work / "tmp").mkdir(parents=True)
        tempfile.tempdir = str(work / "tmp")
        real, dry = _both(
            lambda: io.BytesIO(blob),
            work,
            policy=policy,
            overwrite=overwrite,
            open_kwargs={"streaming": streaming},
        )
        assert dry == real, policy


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
