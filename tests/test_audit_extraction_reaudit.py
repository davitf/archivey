"""Reproducers from the 2026-09-30 extraction re-audit.

The 2026-09 security audit's extraction pass was its narrowest
(``review/archive/2026-09-29-extraction-audit/SUMMARY.md``), so extraction was audited
again: ``internal/extraction.py``, ``internal/filters.py``, link handling, and the CLI
``extract`` / ``test`` paths. Numbering continues the audit's E1-E4.

Each test asserts the promised behaviour. The one still open is
``xfail(strict=True)`` with the defect in ``reason``: a fix makes the test XPASS, which
fails CI until the marker is removed. The rest are regression tests for the fixes.
"""

from __future__ import annotations

import io
import os
import stat
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
@pytest.mark.parametrize(
    ("stem", "entries"),
    [
        # The only top-level entry is itself a link: the move changes the directory
        # its target is read from.
        pytest.param("etc", [("b", "sym", "../etc/passwd")], id="lone-link-top"),
        pytest.param("etc", [("b", "sym", "passwd")], id="lone-link-no-dotdot"),
        # A step up into the wrapper blocks even when it comes back down by name:
        # from the working directory, the same step could reach another name.
        pytest.param(
            "pkg",
            [("top", "dir", None), ("top/a/l", "sym", "../../top/x")],
            id="up-and-back-in",
        ),
        # `a/../passwd` never climbs on paper, but `a` is `top`, so `..` is the
        # wrapper, and after the move the working directory's own `passwd`.
        pytest.param(
            "pkg",
            [
                ("top", "dir", None),
                ("top/sub", "dir", None),
                ("top/sub/a", "sym", ".."),
                ("top/sub/l", "sym", "a/../passwd"),
            ],
            id="chain-hides-a-climb",
        ),
    ],
)
def test_hoist_does_not_move_a_link_whose_meaning_would_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stem: str,
    entries: list,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "passwd").write_text("the operator's file")
    _build_tar(work / f"{stem}.tar", entries)
    monkeypatch.chdir(work)

    main(["x", f"{stem}.tar"])

    # Nothing left the wrapper: the only entry in the working directory besides the
    # operator's file and the archive is the wrapper itself.
    assert sorted(p.name for p in work.iterdir()) == sorted(
        ["passwd", f"{stem}.tar", stem]
    )
    assert f"kept in {stem}/: " in capsys.readouterr().err


@posix_links
@pytest.mark.parametrize("top", ["pkg", "top"], ids=["flatten", "merge-move"])
def test_hoist_moves_a_tree_whose_links_stay_inside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, top: str
) -> None:
    # The ordinary package shape: a relative `..` link that stays inside the tree.
    _build_tar(
        tmp_path / "pkg.tar",
        [
            (top, "dir", None),
            (f"{top}/lib/a.so", "file", b"so"),
            (f"{top}/bin/a", "sym", "../lib/a.so"),
        ],
    )
    monkeypatch.chdir(tmp_path)

    main(["x", "pkg.tar"])

    assert (tmp_path / top / "bin" / "a").read_bytes() == b"so"
    assert not (tmp_path / top / top).exists()


@posix_links
def test_hoist_still_moves_a_tree_whose_links_only_go_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_tar(
        tmp_path / "pkg.tar",
        [
            ("top", "dir", None),
            ("top/x", "file", b"x"),
            ("top/l", "sym", "x"),
            ("top/sub/m", "sym", "./n/o"),
        ],
    )
    monkeypatch.chdir(tmp_path)

    main(["x", "pkg.tar"])

    assert (tmp_path / "top" / "l").read_bytes() == b"x"
    assert not (tmp_path / "pkg").exists()


@posix_links
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
def test_hoist_never_moves_the_operators_own_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    overwrite: str,
) -> None:
    (tmp_path / "backup").mkdir()
    (tmp_path / "backup" / "notes.txt").write_text("operator data")
    _build_tar(tmp_path / "backup.tar", [("../evil", "file", b"x")])
    monkeypatch.chdir(tmp_path)

    main(["x", "--overwrite", overwrite, "backup.tar"])

    assert (tmp_path / "backup" / "notes.txt").read_text() == "operator data"
    assert not (tmp_path / "notes.txt").exists()
    assert "kept in backup/: the folder was already there" in capsys.readouterr().err


# --- Directory members and the caller's directories ---------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.parametrize("policy", ["strict", "standard"])
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

    report = archivey.extract(archive, dest, policy=policy)

    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(dest / "pre").st_mode) == 0o700
    # The results say so, since the tree now differs from the archive.
    kept = {r.member.name: r.kept_mode for r in report.results}
    assert kept == {".": 0o700, "f": None, "pre/": 0o700}


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_a_directory_this_run_created_still_gets_its_mode(tmp_path: Path) -> None:
    # `d/f` creates `d` before the `d/` member arrives; `d` is still the archive's.
    archive = _build_tar(
        tmp_path / "a.tar",
        [("d/f", "file", b"x"), ("d/", "dir", None, {"mode": 0o750})],
    )
    dest = tmp_path / "out"

    report = archivey.extract(archive, dest, policy="standard")

    assert stat.S_IMODE(os.stat(dest / "d").st_mode) == 0o750
    assert all(r.kept_mode is None for r in report.results)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_a_destination_this_run_created_gets_the_root_members_mode(
    tmp_path: Path,
) -> None:
    archive = _build_tar(
        tmp_path / "a.tar",
        [("./", "dir", None, {"mode": 0o750}), ("./f", "file", b"x")],
    )
    dest = tmp_path / "out"

    report = archivey.extract(archive, dest, policy="standard")

    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o750
    assert all(r.kept_mode is None for r in report.results)


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
    if streaming:
        # The source's bytes streamed past and its path now holds `A`: out of reach.
        assert link.status is ExtractionStatus.FAILED
        assert isinstance(link.error, archivey.ExtractionError)
    else:
        # The second pass re-reads the source, as it does for an excluded one.
        assert link.status is ExtractionStatus.EXTRACTED
        assert (dest / "h").read_bytes() == b"ORIGINAL"


@posix_links
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
    # REPLACE removes only an empty directory, as GNU tar does.
    last = report.results[-1]
    assert last.status is ExtractionStatus.FAILED
    assert "not empty" in str(last.error)
    assert (dest / "d" / "f").read_bytes() == b"x"


@posix_links
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
    by_name = {r.member.name: r for r in report.results}
    assert by_name["s/f"].status is ExtractionStatus.OVERWRITTEN
    assert by_name["d/f"].collided_with is not None


@posix_links
@pytest.mark.parametrize("streaming", [False, True])
def test_a_collision_through_a_repointed_symlink_lands_where_the_member_named(
    tmp_path: Path, streaming: bool
) -> None:
    # ``s/f`` claims ``d1/f`` while ``s -> d1``. After ``s`` is repointed to ``d2``, the
    # claim's name ``s/f`` names ``d2/f``, so routing ``d1/f`` to that name wrote it
    # into ``d2``.
    archive = _build_tar(
        tmp_path / "a.tar",
        [
            ("d1", "dir", None),
            ("d2", "dir", None),
            ("s", "sym", "d1"),
            ("s/f", "file", b"A"),
            ("s", "sym", "d2"),
            ("s/f", "file", b"C"),
            ("d1/f", "file", b"D"),
        ],
    )
    dest = tmp_path / "out"

    with archivey.open_archive(archive, streaming=streaming) as reader:
        report = reader.extract_all(
            dest, policy="standard", overwrite="replace", on_error="continue"
        )

    assert (dest / "d1" / "f").read_bytes() == b"D"
    assert (dest / "d2" / "f").read_bytes() == b"C"
    last = report.results[-1]
    assert last.status is ExtractionStatus.EXTRACTED
    assert last.path is not None
    assert os.path.realpath(last.path) == os.path.realpath(dest / "d1" / "f")


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


def test_a_filter_returning_a_non_member_gets_a_type_error_naming_the_filter(
    tmp_path: Path,
) -> None:
    archive = _build_tar(tmp_path / "a.tar", [("f", "file", b"x")])

    with archivey.open_archive(archive) as reader:
        with pytest.raises(TypeError, match="filter"):
            reader.extract_all(tmp_path / "out", filter=lambda member: True)


# --- Directory sources --------------------------------------------------------------


@pytest.mark.parametrize("streaming", [False, True])
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
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    ("entries", "target"),
    [
        # The symlink's target passes through another symlinked directory, where a
        # lookup by member name finds nothing.
        pytest.param(
            [("y/x", "file", b"YX"), ("sub", "sym", "y"), ("s", "sym", "sub/x")],
            "sub/x",
            id="through-a-symlinked-directory",
        ),
        pytest.param(
            [("sub/x", "file", b"YX"), ("s", "sym", "sub/x")],
            "sub/x",
            id="plain-chain",
        ),
    ],
)
def test_a_hardlink_to_a_symlink_is_a_second_symlink(
    tmp_path: Path, streaming: bool, entries: list, target: str
) -> None:
    # GNU tar makes `h` a second name for the symlink `s`, so `h` is a symlink with
    # the same target.
    archive = _build_tar(tmp_path / "a.tar", [*entries, ("h", "hard", "s")])
    dest = tmp_path / "out"

    with archivey.open_archive(archive, streaming=streaming) as reader:
        report = reader.extract_all(dest, policy="standard", on_error="continue")

    assert {r.member.name: r.status for r in report.results}["h"] is (
        ExtractionStatus.EXTRACTED
    )
    assert os.readlink(dest / "h") == target
    assert (dest / "h").read_bytes() == b"YX"


@posix_links
def test_a_hardlink_to_an_escaping_symlink_is_blocked_like_it(tmp_path: Path) -> None:
    archive = _build_tar(
        tmp_path / "a.tar",
        [("s", "sym", "../outside"), ("h", "hard", "s")],
    )
    dest = tmp_path / "out"

    report = archivey.extract(archive, dest, policy="standard")

    statuses = {r.member.name: r.status for r in report.results}
    assert statuses == {"s": ExtractionStatus.BLOCKED, "h": ExtractionStatus.BLOCKED}
    assert not os.path.lexists(dest / "h")
