"""Reproducers from the 2026-09-30 extraction re-audit.

The 2026-09 security audit's extraction pass was its narrowest
(``review/archive/2026-09-29-extraction-audit/SUMMARY.md``), so extraction was audited
again: ``internal/extraction.py``, ``internal/filters.py``, link handling, and the CLI
``extract`` / ``test`` paths. Numbering continues the audit's E1-E4.

Each test asserts the promised behaviour and is ``xfail(strict=True)`` with the defect
in ``reason``. A fix makes the test XPASS, which fails CI until the marker is removed.
Triage is pending: nothing here is fixed yet.
"""

from __future__ import annotations

import io
import os
import stat
import sys
import tarfile
from pathlib import Path

import pytest

import archivey
from archivey import ExtractionStatus
from archivey.cli.main import main

posix_links = pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")


def _build_tar(path: Path, entries: list[tuple]) -> Path:
    """Write a TAR of ``(name, kind, payload[, TarInfo attributes])`` entries.

    ``kind`` is ``file`` (payload is the content), ``sym`` / ``hard`` (payload is the
    link name) or ``dir``.
    """
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tf:
        for name, kind, payload, *rest in entries:
            info = tarfile.TarInfo(name)
            info.mode = 0o755 if kind == "dir" else 0o644
            for key, value in (rest[0] if rest else {}).items():
                setattr(info, key, value)
            if kind == "file":
                info.size = len(payload)
                tf.addfile(info, io.BytesIO(payload))
                continue
            info.type = {
                "sym": tarfile.SYMTYPE,
                "hard": tarfile.LNKTYPE,
                "dir": tarfile.DIRTYPE,
            }[kind]
            if kind in ("sym", "hard"):
                info.linkname = payload
            tf.addfile(info)
    return path


def _links_escaping(root: Path) -> list[str]:
    """Every symlink under ``root`` that resolves outside it."""
    real_root = root.resolve()
    escaping = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                resolved = path.resolve()
                if not (resolved == real_root or resolved.is_relative_to(real_root)):
                    escaping.append(f"{path} -> {os.readlink(path)} = {resolved}")
    return escaping


# --- CLI: the single-root hoist ---------------------------------------------------


@posix_links
@pytest.mark.xfail(
    strict=True,
    reason="E5 (high): the CLI hoist moves an extracted tree up one level after the "
    "library checked its symlinks. A link that re-entered the wrapper directory by "
    "name (`../../<stem>/x`) then resolves to `<cwd>/../<stem>/x`, outside the "
    "destination, and the run exits 0",
)
def test_hoist_leaves_no_symlink_resolving_outside_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "authorized_keys").write_text("keys")
    downloads = home / "Downloads"
    downloads.mkdir()
    # No member index for TAR, so `archivey x` extracts into ./.ssh/ (named after the
    # archive) and hoists the single top-level entry `top` into the working directory.
    _build_tar(
        downloads / ".ssh.tar",
        [
            ("top", "dir", None),
            ("top/k", "sym", "../../.ssh/authorized_keys"),
            ("top/f", "file", b"data"),
        ],
    )
    monkeypatch.chdir(downloads)

    main(["x", ".ssh.tar"])

    assert _links_escaping(downloads) == []


@posix_links
@pytest.mark.xfail(
    strict=True,
    reason="E5b: the hoist's in-place flatten (`src.tar` holding `src/`) also moves "
    "links up one level; `src/src/l -> ../x` pointed inside `src/`, and after the "
    "flatten `src/l` points at the working directory's own `x`",
)
def test_hoist_flatten_keeps_links_inside_the_reported_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "outside.txt").write_text("the operator's file")
    _build_tar(
        tmp_path / "src.tar",
        [("src", "dir", None), ("src/l", "sym", "../outside.txt")],
    )
    monkeypatch.chdir(tmp_path)

    main(["x", "src.tar"])

    # The summary line reports `src/` as the destination.
    assert _links_escaping(tmp_path / "src") == []


@pytest.mark.parametrize("overwrite", ["error", "skip", "replace"])
@pytest.mark.xfail(
    strict=True,
    reason="E6: when a directory named like the archive already exists, the CLI "
    "extracts into it and then hoists its only child. If the archive added nothing "
    "(every member blocked, or an empty archive), that child is the operator's own "
    "file: it is moved into the working directory and their directory is removed",
)
def test_hoist_never_moves_the_operators_own_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overwrite: str
) -> None:
    (tmp_path / "backup").mkdir()
    (tmp_path / "backup" / "notes.txt").write_text("operator data")
    _build_tar(tmp_path / "backup.tar", [("../evil", "file", b"x")])
    monkeypatch.chdir(tmp_path)

    main(["x", "--overwrite", overwrite, "backup.tar"])

    assert (tmp_path / "backup" / "notes.txt").read_text() == "operator data"
    assert not (tmp_path / "notes.txt").exists()


# --- Directory members and the caller's directories ---------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.parametrize("policy", ["strict", "standard"])
@pytest.mark.xfail(
    strict=True,
    reason="E7: a directory member whose destination already exists gets the "
    "member's mode, so `./` (what `tar -C dir -cf x.tar .` writes) chmods the "
    "destination root: STRICT widens a 0o700 destination to 0o755, STANDARD to a "
    "world-writable 0o777. The same happens to any pre-existing directory",
)
def test_a_directory_member_never_widens_an_existing_directory(
    tmp_path: Path, policy: str
) -> None:
    archive = _build_tar(
        tmp_path / "a.tar",
        [
            ("./", "dir", None, {"mode": 0o777}),
            ("./f", "file", b"x"),
            ("pre/", "dir", None, {"mode": 0o777}),
        ],
    )
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "pre").mkdir()
    os.chmod(dest, 0o700)
    os.chmod(dest / "pre", 0o700)

    archivey.extract(archive, dest, policy=policy)

    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(dest / "pre").st_mode) == 0o700


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.xfail(
    strict=True,
    reason="Known, tracked internally with the directory-permission deferral: a "
    "directory's stored mtime is applied when it is created, and every child "
    "written after it moves the mtime to now. tar and tarfile set directory "
    "metadata last",
)
def test_a_directory_keeps_its_stored_mtime(tmp_path: Path, streaming: bool) -> None:
    archive = _build_tar(
        tmp_path / "a.tar",
        [
            ("d", "dir", None, {"mtime": 1_000_000_000}),
            ("d/f", "file", b"x", {"mtime": 1_000_000_000}),
        ],
    )
    dest = tmp_path / "out"

    with archivey.open_archive(archive, streaming=streaming) as reader:
        reader.extract_all(dest)

    assert os.stat(dest / "d").st_mtime == 1_000_000_000


# --- Hardlinks after REPLACE ----------------------------------------------------------


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.xfail(
    strict=True,
    reason="E8: a hardlink is made to the path its source member was written at, "
    "even after a later member replaced that path. Under STANDARD + REPLACE, `A` "
    "replaces `a`, and `h` (a link to `a`) gets `A`'s content. Positional link "
    "resolution exists so that a later member cannot redirect a link",
)
def test_hardlink_to_a_replaced_source_does_not_get_the_replacements_content(
    tmp_path: Path, streaming: bool
) -> None:
    archive = _build_tar(
        tmp_path / "a.tar",
        [("a", "file", b"ORIGINAL"), ("A", "file", b"OTHER"), ("h", "hard", "a")],
    )
    dest = tmp_path / "out"

    with archivey.open_archive(archive, streaming=streaming) as reader:
        report = reader.extract_all(
            dest, policy="standard", overwrite="replace", on_error="continue"
        )

    link = {r.member.name: r for r in report.results}["h"]
    # Either the link carries its source's content, or it fails; never another's.
    if link.status is ExtractionStatus.EXTRACTED:
        assert (dest / "h").read_bytes() == b"ORIGINAL"


@posix_links
@pytest.mark.xfail(
    strict=True,
    reason="E8b: when the member that replaced the source is a symlink, `os.link` "
    "follows it, so the archive's hardlink becomes a second name for a file that "
    "was in the destination before the run",
)
def test_hardlink_never_links_a_file_the_run_did_not_write(tmp_path: Path) -> None:
    archive = _build_tar(
        tmp_path / "a.tar",
        [("a", "file", b"ORIGINAL"), ("A", "sym", "victim"), ("h", "hard", "a")],
    )
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "victim").write_bytes(b"caller's file")

    archivey.extract(
        archive, dest, policy="standard", overwrite="replace", on_error="continue"
    )

    if (dest / "h").exists():
        assert not os.path.samefile(dest / "h", dest / "victim")


# --- Results that say EXTRACTED for content that is gone --------------------------


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.xfail(
    strict=True,
    reason="E9: REPLACE removes a directory with `rmtree`, including members this "
    "run wrote into it, and their results stay EXTRACTED. The spec: `a result SHALL "
    "NOT report EXTRACTED for content that no longer exists`",
)
def test_replacing_a_directory_revises_the_members_written_inside_it(
    tmp_path: Path, streaming: bool
) -> None:
    archive = _build_tar(
        tmp_path / "a.tar",
        [("d", "dir", None), ("d/f", "file", b"x"), ("d", "file", b"FILE")],
    )
    dest = tmp_path / "out"

    with archivey.open_archive(archive, streaming=streaming) as reader:
        report = reader.extract_all(dest, overwrite="replace", on_error="continue")

    for result in report.results:
        if result.status is ExtractionStatus.EXTRACTED:
            assert result.path is not None
            assert os.path.lexists(result.path), result.member.name
    extracted = [r for r in report.results if r.status is ExtractionStatus.EXTRACTED]
    assert len({r.path for r in extracted}) == len(extracted)


@posix_links
@pytest.mark.xfail(
    strict=True,
    reason="E10: the collision map keys on the member name, so `s/f` (through the "
    "archive's own `s -> d`) and `d/f` are one file that two EXTRACTED results "
    "claim; the first one's content is gone. docs/extracting.md: REPLACE `is not a "
    "silent merge`",
)
def test_two_members_written_to_one_file_through_a_symlink_are_not_both_extracted(
    tmp_path: Path,
) -> None:
    archive = _build_tar(
        tmp_path / "a.tar",
        [
            ("d", "dir", None),
            ("s", "sym", "d"),
            ("s/f", "file", b"FIRST"),
            ("d/f", "file", b"SECOND"),
        ],
    )

    report = archivey.extract(archive, tmp_path / "out", overwrite="replace")

    files = [
        r
        for r in report.results
        if r.member.type is archivey.MemberType.FILE
        and r.status is ExtractionStatus.EXTRACTED
    ]
    assert len({os.path.realpath(r.path) for r in files}) == len(files)


# --- Untyped errors -----------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="Linux name limits")
@pytest.mark.parametrize(
    "entry",
    [
        pytest.param(("x" * 300, "file", b"x"), id="component-over-255-bytes"),
        pytest.param(
            ("/".join(["d" * 200] * 25) + "/f", "file", b"x"), id="path-over-4096"
        ),
        pytest.param(("s", "sym", "t" * 5000), id="link-target-over-4096"),
    ],
)
@pytest.mark.xfail(
    strict=True,
    reason="E11: a name or link target longer than the filesystem allows raises a "
    "raw OSError(ENAMETOOLONG), which ends `extract()` under the default "
    "OnError.STOP. Like EILSEQ (already typed) and E3/E4, the limit is hit because of "
    "the archive, not the filesystem's state",
)
def test_a_name_too_long_for_the_filesystem_is_a_typed_member_failure(
    tmp_path: Path, entry: tuple
) -> None:
    archive = _build_tar(tmp_path / "a.tar", [entry, ("ok", "file", b"y")])

    try:
        report = archivey.extract(archive, tmp_path / "out")
    except archivey.ArchiveyError:
        return  # a typed stop is fine
    first = report.results[0]
    assert first.error is None or isinstance(first.error, archivey.ArchiveyError)


@posix_links
@pytest.mark.xfail(
    sys.version_info < (3, 13),
    strict=True,
    reason="E12: a symlink loop already in the destination makes `Path.resolve()` in "
    "`check_universal` raise a raw RuntimeError on Python 3.11/3.12. It is neither "
    "an ArchiveyError nor an OSError, so it escapes `on_error='continue'`",
)
def test_a_symlink_loop_in_the_destination_is_not_a_raw_runtime_error(
    tmp_path: Path,
) -> None:
    archive = _build_tar(
        tmp_path / "a.tar", [("x/f", "file", b"x"), ("ok", "file", b"y")]
    )
    dest = tmp_path / "out"
    dest.mkdir()
    os.symlink("x", dest / "x")  # x -> x

    report = archivey.extract(archive, dest, on_error="continue")

    assert {r.member.name: r.status for r in report.results}["ok"] is (
        ExtractionStatus.EXTRACTED
    )


@pytest.mark.xfail(
    strict=True,
    reason="E14: a filter that returns something other than an ArchiveMember or None "
    "fails with a raw AttributeError from inside the coordinator, naming neither "
    "the filter nor what it returned",
)
def test_a_filter_returning_a_non_member_gets_a_type_error_naming_the_filter(
    tmp_path: Path,
) -> None:
    archive = _build_tar(tmp_path / "a.tar", [("f", "file", b"x")])

    with archivey.open_archive(archive) as reader:
        with pytest.raises(TypeError, match="filter"):
            reader.extract_all(tmp_path / "out", filter=lambda member: True)


# --- Directory sources --------------------------------------------------------------


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.xfail(
    strict=True,
    reason="E13: extracting a directory source into a directory inside it reads its "
    "own output. Streaming recurses (`copy/copy/copy/...`) until the path is too "
    "long; random access lists the new destination as a member. `cp -r` refuses "
    "the same request",
)
def test_a_directory_source_is_not_extracted_into_itself(
    tmp_path: Path, streaming: bool
) -> None:
    source = tmp_path / "src"
    (source / "sub").mkdir(parents=True)
    (source / "a.txt").write_text("a")
    (source / "sub" / "b.txt").write_text("b")

    names: list[str] = []

    class _Runaway(Exception):
        pass

    def on_progress(progress: archivey.ExtractionProgress) -> None:
        # The source has four entries; a streaming pass reading its own output would
        # otherwise run until the path is too long (tens of seconds).
        names.append(progress.member.name)
        if len(names) > 20:
            raise _Runaway

    try:
        with archivey.open_archive(source, streaming=streaming) as reader:
            reader.extract_all(
                source / "copy", on_error="continue", on_progress=on_progress
            )
    except archivey.ArchiveyError:
        return  # refusing the call is fine
    except _Runaway:
        pass
    assert not [name for name in names if name.startswith("copy")]


# --- Links to links -----------------------------------------------------------------


@posix_links
@pytest.mark.xfail(
    strict=True,
    reason="E15 (low): a TAR hardlink to a symlink member is resolved by following "
    "the symlink through the member list, by name. When the symlink goes through "
    "another symlinked directory, that lookup finds no member and the hardlink "
    "fails. GNU tar links the symlink itself",
)
def test_a_hardlink_to_a_symlink_through_a_symlinked_directory_is_extracted(
    tmp_path: Path,
) -> None:
    archive = _build_tar(
        tmp_path / "a.tar",
        [
            ("y/x", "file", b"YX"),
            ("sub", "sym", "y"),
            ("s", "sym", "sub/x"),
            ("h", "hard", "s"),
        ],
    )
    dest = tmp_path / "out"

    report = archivey.extract(archive, dest, policy="standard", on_error="continue")

    assert {r.member.name: r.status for r in report.results}["h"] is (
        ExtractionStatus.EXTRACTED
    )
    assert (dest / "h").read_bytes() == b"YX"
