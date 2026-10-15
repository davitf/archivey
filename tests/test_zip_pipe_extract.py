"""Extracting a ZIP read from a source that cannot seek.

The pass writes each member as its local header describes it; the central directory,
read after the last member, then settles the mode, type and name of what was written
(``ExtractionCoordinator._settle_revised_members``). Each test extracts the same bytes
from a file and from a pipe and expects the same files on disk and the same results.
"""

from __future__ import annotations

import contextlib
import io
import os
import stat
import struct
import time
import zipfile
from pathlib import Path

import pytest

import archivey
from archivey.config import ExtractionLimits
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    ExtractionError,
    FilterRejectionError,
)
from archivey.internal.extraction import BombTracker
from archivey.types import (
    AbortOn,
    ArchiveMember,
    ExtractionPolicy,
    ExtractionStatus,
    MemberType,
    NameRewrite,
    OnError,
    OverwritePolicy,
)
from tests.sample_archives import CORPUS, build_archive, skip_unless_runnable
from tests.streams_util import NonSeekableBytesIO
from tests.test_zip_pipe import _Hand, _planted_prefix_zip, _reparse_zip, _streamed

needs_symlinks = pytest.mark.skipif(
    not hasattr(os, "symlink") or os.name == "nt", reason="needs POSIX symlinks"
)
# Also where a backslash in a name is no separator.
posix_modes = pytest.mark.skipif(os.name == "nt", reason="needs POSIX")


def _entry(name: str, *, mode: int | None = None, host: int = 3) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 2, 3, 4, 6))
    info.create_system = host
    if mode is not None:
        info.external_attr = mode << 16
    return info


def _zip(entries: list[tuple[zipfile.ZipInfo, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for info, data in entries:
            zf.writestr(info, data)
    return buf.getvalue()


def _tree(
    root: Path, *, times: bool = True, since: float | None = None
) -> dict[str, tuple[object, ...]]:
    """Each entry under ``root``: its bytes, link target or ``dir``, and for a file or
    directory its mode and modification time (a link gets no metadata applied).

    A time from ``since`` on is the run's own, not the archive's (a folder no
    member names, or one of the caller's), and two runs can straddle a second, so
    it reads ``"now"``. ``times=False`` leaves the times out."""
    tree: dict[str, tuple[object, ...]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            tree[rel] = ("symlink", os.readlink(path))
            continue
        st = path.stat()
        content = "dir" if path.is_dir() else path.read_bytes()
        mtime: object = int(st.st_mtime)
        if since is not None and st.st_mtime >= since:
            mtime = "now"
        entry = (content, stat.S_IMODE(st.st_mode), mtime)
        tree[rel] = entry if times else entry[:2]
    return tree


def _extract(
    data: bytes, dest: Path, *, piped: bool, **options: object
) -> list[tuple[str, ExtractionStatus, str | None]]:
    source = NonSeekableBytesIO(data) if piped else io.BytesIO(data)
    with archivey.open_archive(source, streaming=piped) as reader:
        report = reader.extract_all(dest, **options)  # type: ignore[arg-type]
    return [
        (
            r.member.name,
            r.status,
            None if r.path is None else r.path.relative_to(dest).as_posix(),
        )
        for r in report.results
    ]


def _both(
    data: bytes, tmp_path: Path, *, times: bool = True, **options: object
) -> tuple[
    list[tuple[str, ExtractionStatus, str | None]], dict[str, tuple[object, ...]]
]:
    """Extract ``data`` from a file and from a pipe; assert both end the same, and
    return the results and the tree. ``times=False`` leaves the times out, for a
    destination the caller seeded: a folder of theirs gets the time of the run."""
    started = time.time() - 1
    from_file = _extract(data, tmp_path / "file", piped=False, **options)
    from_pipe = _extract(data, tmp_path / "pipe", piped=True, **options)
    tree = _tree(tmp_path / "file", times=times, since=started)
    piped_tree = _tree(tmp_path / "pipe", times=times, since=started)
    assert from_pipe == from_file
    assert piped_tree == tree
    return from_file, tree


@posix_modes
@pytest.mark.parametrize("policy", list(ExtractionPolicy))
@pytest.mark.parametrize("written", ["sized", "streamed"])
def test_modes_from_the_directory_reach_the_files(
    policy: ExtractionPolicy, written: str, tmp_path: Path
) -> None:
    data = _zip(
        [
            (_entry("tool/", mode=0o40700), b""),
            (_entry("tool/run.sh", mode=0o100755), b"#!/bin/sh\n"),
            (_entry("tool/secret", mode=0o100600), b"key"),
            (_entry("plain.txt", host=0), b"no mode"),
        ]
    )
    if written == "streamed":
        data = _streamed(data)
    results, tree = _both(data, tmp_path, policy=policy)
    assert {s for _, s, _ in results} == {ExtractionStatus.EXTRACTED}
    if policy is not ExtractionPolicy.STRICT:
        assert tree["tool/run.sh"][:2] == (b"#!/bin/sh\n", 0o755)


def test_a_time_only_the_directory_holds_is_applied(tmp_path: Path) -> None:
    """The central entry's extended-timestamp field gives the modification time; the
    local header's copy is renamed to a field no reader knows, so the pass writes
    the DOS time first and the directory's time replaces it."""
    when = 1_600_000_000
    info = _entry("a.txt", mode=0o100644)
    info.extra = struct.pack("<HHBl", 0x5455, 5, 1, when)
    data = bytearray(_zip([(info, b"x")]))
    name_len, extra_len = struct.unpack_from("<HH", data, 26)
    assert extra_len == 9
    data[30 + name_len : 30 + name_len + 2] = b"\x99\x99"
    _, tree = _both(bytes(data), tmp_path, policy="trusted")
    assert tree["a.txt"][2] == when


def test_a_filter_that_set_a_mode_keeps_it(tmp_path: Path) -> None:
    def chmod(member: ArchiveMember) -> ArchiveMember:
        return member.replace(mode=0o640) if member.name == "a" else member

    data = _zip(
        [(_entry("a", mode=0o100755), b"x"), (_entry("b", mode=0o100755), b"y")]
    )
    _, tree = _both(data, tmp_path, policy="trusted", filter=chmod)
    if os.name != "nt":
        assert tree["a"][:2] == (b"x", 0o640)
        assert tree["b"][:2] == (b"y", 0o755)


def _link_zip(target: str, *, name: str = "link") -> bytes:
    return _zip(
        [
            (_entry("a.txt", mode=0o100644), b"hello"),
            (_entry(name, mode=0o120777), target.encode()),
        ]
    )


@needs_symlinks
def test_a_member_the_directory_types_as_a_link_becomes_one(tmp_path: Path) -> None:
    results, tree = _both(_link_zip("a.txt"), tmp_path, policy="standard")
    assert tree["link"] == ("symlink", "a.txt")
    assert results[1] == ("link", ExtractionStatus.EXTRACTED, "link")


@pytest.mark.parametrize("written", ["sized", "streamed"])
def test_a_link_out_of_the_destination_is_refused_from_a_pipe(
    written: str, tmp_path: Path
) -> None:
    """The local header says a file; the directory says a link that escapes. The
    file written for it is removed, as the refusal from a file writes nothing."""
    data = _link_zip("../../etc/passwd", name="payload")
    if written == "streamed":
        data = _streamed(data)
    results, tree = _both(data, tmp_path, policy="standard", on_error="continue")
    assert results[1][:2] == ("payload", ExtractionStatus.BLOCKED)
    assert "payload" not in tree


def test_a_refused_link_stops_the_run_under_abort_on(tmp_path: Path) -> None:
    from archivey.exceptions import FilterRejectionError

    data = _link_zip("../../etc/passwd", name="payload")
    for piped in (False, True):
        dest = tmp_path / str(piped)
        with pytest.raises(FilterRejectionError):
            _extract(data, dest, piped=piped, abort_on=["blocked_member"])
        assert not (dest / "payload").exists()


def test_a_filter_that_takes_only_files_drops_the_link(tmp_path: Path) -> None:
    def files_only(member: ArchiveMember) -> ArchiveMember | None:
        return member if member.type is MemberType.FILE else None

    results, tree = _both(_link_zip("a.txt"), tmp_path, filter=files_only)
    assert [name for name, _, _ in results] == ["a.txt"]
    assert sorted(tree) == ["a.txt"]


@needs_symlinks
def test_a_filter_that_takes_only_links_keeps_the_link(tmp_path: Path) -> None:
    """The pass sees a provisional file and the filter drops it; the end of the pass
    asks the filter again about the link it became, whose target the reader kept."""

    def links_only(member: ArchiveMember) -> ArchiveMember | None:
        return member if member.type is MemberType.SYMLINK else None

    results, tree = _both(_link_zip("a.txt"), tmp_path, filter=links_only)
    assert [name for name, _, _ in results] == ["link"]
    assert tree == {"link": ("symlink", "a.txt")}


@needs_symlinks
def test_a_members_predicate_that_takes_only_links_keeps_the_link(
    tmp_path: Path,
) -> None:
    results, tree = _both(
        _link_zip("a.txt"),
        tmp_path,
        members=lambda member: member.type is MemberType.SYMLINK,
    )
    assert [name for name, _, _ in results] == ["link"]
    assert tree == {"link": ("symlink", "a.txt")}


def _dos_docs() -> bytes:
    return _zip(
        [
            (_entry("docs\\", host=0), b""),
            (_entry("docs\\r.txt", host=0), b"read me"),
            (_entry("other.txt", host=0), b"other"),
        ]
    )


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["docs/r.txt"], ["docs/r.txt"]),
        # The name a DOS host's directory never gives: nothing matches, from a pipe
        # too, though the pass wrote the member before it knew.
        (["docs\\r.txt"], []),
        (["docs/"], ["docs/"]),
    ],
)
def test_members_by_name_select_what_the_directory_names(
    names: list[str], expected: list[str], tmp_path: Path
) -> None:
    results, tree = _both(_dos_docs(), tmp_path, members=names)
    assert [name for name, _, _ in results] == expected
    assert ("docs/r.txt" in tree) == ("docs/r.txt" in expected)


def test_a_filter_by_a_name_the_directory_gives_fails_from_a_pipe(
    tmp_path: Path,
) -> None:
    """The pass offers the filter ``docs\\r.txt``, which it drops; the data has
    gone by when the directory names it ``docs/r.txt`` (``design.md``)."""

    def docs_only(member: ArchiveMember) -> ArchiveMember | None:
        return member if member.name.startswith("docs/") else None

    from_file = _extract(_dos_docs(), tmp_path / "file", piped=False, filter=docs_only)
    from_pipe = _extract(
        _dos_docs(), tmp_path / "pipe", piped=True, filter=docs_only,
        on_error="continue",
    )  # fmt: skip
    assert [(n, s) for n, s, _ in from_file] == [
        ("docs/", ExtractionStatus.EXTRACTED),
        ("docs/r.txt", ExtractionStatus.EXTRACTED),
    ]
    # On Windows the filter already sees docs/r.txt during the pass (CI), so it takes
    # the member while its data is still in the stream.
    assert [(n, s) for n, s, _ in from_pipe] == [
        ("docs/", ExtractionStatus.EXTRACTED),
        (
            "docs/r.txt",
            ExtractionStatus.EXTRACTED if os.name == "nt" else ExtractionStatus.FAILED,
        ),
    ]


def test_a_member_dropped_for_its_data_is_an_extraction_error(tmp_path: Path) -> None:
    """The archive is sound: the pass cannot go back for the bytes."""

    def docs_only(member: ArchiveMember) -> ArchiveMember | None:
        return member if member.name.startswith("docs/") else None

    source = NonSeekableBytesIO(_dos_docs())
    with archivey.open_archive(source, streaming=True) as reader:
        report = reader.extract_all(tmp_path, filter=docs_only, on_error="continue")
    errors = [r.error for r in report.results if r.error is not None]
    if os.name == "nt":
        assert errors == []
    else:
        assert len(errors) == 1
        assert type(errors[0]) is ExtractionError


def _seed(dest: Path, files: dict[str, bytes]) -> None:
    for name, content in files.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


@posix_modes
@pytest.mark.parametrize("overwrite", ["replace", "skip", "rename", "error"])
def test_a_member_the_list_leaves_out_in_the_end_keeps_the_callers_file(
    overwrite: str, tmp_path: Path
) -> None:
    """The pass writes ``x\\y`` at ``x/y`` while the list could take it by its DOS
    spelling; a Unix host leaves it ``x\\y``, which the list does not take, and
    what the caller had at ``x/y`` is back."""
    data = _zip([(_entry("x\\y"), b"ARCHIVE")])
    for where in ("file", "pipe"):
        _seed(tmp_path / where, {"x/y": b"MINE"})
    results, tree = _both(
        data,
        tmp_path,
        times=False,
        members=["x/y"],
        overwrite=overwrite,
        on_error="continue",
    )
    assert results == []
    assert tree["x/y"][0] == b"MINE"
    assert sorted(tree) == ["x", "x/y"]


@posix_modes
@pytest.mark.parametrize("overwrite", ["replace", "skip", "rename", "error"])
def test_a_member_the_list_leaves_out_in_the_end_keeps_the_callers_parent(
    overwrite: str, tmp_path: Path
) -> None:
    """As above, with the caller's file at ``x``, which ``x/y`` implies is a
    directory: the pass makes ``x/`` there, and the caller's ``x`` is back."""
    data = _zip([(_entry("x\\y"), b"ARCHIVE")])
    for where in ("file", "pipe"):
        _seed(tmp_path / where, {"x": b"MINE"})
    results, tree = _both(
        data,
        tmp_path,
        times=False,
        members=["x/y"],
        overwrite=overwrite,
        on_error="continue",
    )
    assert results == []
    assert tree == {"x": (b"MINE", 0o644)}


@pytest.mark.parametrize("overwrite", ["replace", "skip", "rename", "error"])
def test_a_member_the_list_takes_in_the_end_meets_the_callers_parent(
    overwrite: str, tmp_path: Path
) -> None:
    """``docs\\r.txt`` implies ``docs/``, where the caller has a file: the policy
    decides, from a pipe as from a file."""
    for where in ("file", "pipe"):
        _seed(tmp_path / where, {"docs": b"MINE"})
    data = _zip([(_entry("docs\\r.txt", host=0), b"read me")])
    results, tree = _both(
        data,
        tmp_path,
        times=False,
        members=["docs/r.txt"],
        overwrite=overwrite,
        on_error="continue",
    )
    expected = {
        "replace": ["docs", "docs/r.txt"],
        "rename": ["docs", "docs (1)", "docs (1)/r.txt"],
    }.get(overwrite, ["docs"])
    assert sorted(tree) == expected
    if overwrite != "replace":
        assert tree["docs"][0] == b"MINE"


@posix_modes
def test_a_run_stopped_in_the_pass_puts_the_callers_parent_back(
    tmp_path: Path,
) -> None:
    """As ``test_a_run_stopped_in_the_pass_puts_the_callers_file_back``, with the
    caller's file at the parent ``x`` that ``x/y`` implies."""
    data = _zip([(_entry("x\\y"), b"ARCHIVE"), (_entry("../evil"), b"Z")])
    for where, piped in (("file", False), ("pipe", True)):
        _seed(tmp_path / where, {"x": b"MINE"})
        with pytest.raises(ArchiveyError):
            _extract(
                data,
                tmp_path / where,
                piped=piped,
                members=["x/y", "../evil"],
                overwrite="replace",
                abort_on=["blocked_member"],
            )
        assert (tmp_path / where / "x").read_bytes() == b"MINE"
        assert sorted(p.name for p in (tmp_path / where).iterdir()) == ["x"]


def test_a_stopped_run_that_cannot_take_back_the_parent_keeps_the_callers_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As ``test_a_run_stopped_in_the_pass_puts_the_callers_parent_back``, where
    the filesystem refuses to remove ``x/y`` (Windows, for a read-only member):
    the run's ``x`` stays, so the caller's file stays where it was moved aside
    rather than being deleted."""
    data = _zip([(_entry("x\\y"), b"ARCHIVE"), (_entry("../evil"), b"Z")])
    dest = tmp_path / "pipe"
    _seed(dest, {"x": b"MINE"})
    refused = dest / "x" / "y"
    real_unlink = os.unlink

    def unlink(path: object, *args: object, **kwargs: object) -> None:
        if Path(os.fspath(path)) == refused:  # type: ignore[arg-type]
            raise PermissionError(13, "Access is denied", os.fspath(path))  # type: ignore[arg-type]
        real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "unlink", unlink)
    with pytest.raises(ArchiveyError):
        _extract(
            data,
            dest,
            piped=True,
            members=["x/y", "../evil"],
            overwrite="replace",
            abort_on=["blocked_member"],
        )
    monkeypatch.undo()
    kept = [p for p in dest.iterdir() if p.is_file()]
    assert [p.read_bytes() for p in kept] == [b"MINE"]
    assert (dest / "x" / "y").read_bytes() == b"ARCHIVE"


def test_a_read_only_file_of_the_callers_is_handled_as_from_a_file(
    tmp_path: Path,
) -> None:
    """Windows refuses to replace it from a file; a pipe must not move it aside."""
    for where in ("file", "pipe"):
        _seed(tmp_path / where, {"docs/r.txt": b"MINE"})
        os.chmod(tmp_path / where / "docs/r.txt", stat.S_IREAD)
    try:
        results, tree = _both(
            _dos_docs(),
            tmp_path,
            times=False,
            members=["docs/r.txt"],
            overwrite="replace",
            on_error="continue",
        )
    finally:
        for where in ("file", "pipe"):
            os.chmod(tmp_path / where / "docs/r.txt", stat.S_IREAD | stat.S_IWRITE)
    assert sorted(tree) == ["docs", "docs/r.txt"]


@posix_modes
@pytest.mark.parametrize(
    ("second", "options"),
    [
        ("../evil", {"abort_on": ["blocked_member"]}),
        ("z.txt", {"limits": ExtractionLimits(max_entries=1)}),
    ],
)
def test_a_run_stopped_in_the_pass_puts_the_callers_file_back(
    second: str, options: dict[str, object], tmp_path: Path
) -> None:
    """The run stops before the directory says whether ``x\\y`` is ``x/y``, so
    nothing the pass wrote at the caller's ``x/y`` is kept."""
    data = _zip([(_entry("x\\y"), b"ARCHIVE"), (_entry(second), b"Z")])
    for where, piped in (("file", False), ("pipe", True)):
        _seed(tmp_path / where, {"x/y": b"MINE"})
        # From a file the entry limit is never reached: x\\y is not selected.
        stops = piped or "abort_on" in options
        with pytest.raises(ArchiveyError) if stops else contextlib.nullcontext():
            _extract(
                data,
                tmp_path / where,
                piped=piped,
                members=["x/y", second],
                overwrite="replace",
                **options,
            )
        assert (tmp_path / where / "x/y").read_bytes() == b"MINE"
        assert sorted(p.name for p in (tmp_path / where / "x").iterdir()) == ["y"]


@needs_symlinks
def test_a_run_stopped_while_settling_keeps_what_it_settled(tmp_path: Path) -> None:
    """The directory retypes ``payload`` as a link out of the destination, which
    stops the run; ``docs/r.txt``, settled before it, replaced the caller's file."""
    data = _zip(
        [
            (_entry("docs\\r.txt", host=0), b"read me"),
            (_entry("payload", mode=0o120777), b"../../etc/passwd"),
        ]
    )
    for where, piped in (("file", False), ("pipe", True)):
        _seed(tmp_path / where, {"docs/r.txt": b"MINE"})
        with pytest.raises(ArchiveyError):
            _extract(
                data,
                tmp_path / where,
                piped=piped,
                members=["docs/r.txt", "payload"],
                overwrite="replace",
                abort_on=["blocked_member"],
            )
        assert (tmp_path / where / "docs/r.txt").read_bytes() == b"read me"


@pytest.mark.parametrize("overwrite", ["replace", "skip"])
def test_a_member_the_list_takes_in_the_end_replaces_the_callers_file(
    overwrite: str, tmp_path: Path
) -> None:
    for where in ("file", "pipe"):
        _seed(tmp_path / where, {"docs/r.txt": b"MINE"})
    results, tree = _both(
        _dos_docs(), tmp_path, times=False, members=["docs/r.txt"], overwrite=overwrite
    )
    expected = b"read me" if overwrite == "replace" else b"MINE"
    assert tree["docs/r.txt"][0] == expected
    assert sorted(tree) == ["docs", "docs/r.txt"]


@needs_symlinks
def test_a_predicate_that_drops_the_member_in_the_end_keeps_the_callers_file(
    tmp_path: Path,
) -> None:
    """``link`` is a file until the directory says it is a link."""
    for where in ("file", "pipe"):
        _seed(tmp_path / where, {"link": b"MINE"})
    results, tree = _both(
        _link_zip("a.txt"),
        tmp_path,
        times=False,
        members=lambda member: member.type is MemberType.FILE,
        overwrite="replace",
    )
    assert [name for name, _, _ in results] == ["a.txt"]
    assert tree["link"][0] == b"MINE"
    assert sorted(tree) == ["a.txt", "link"]


@needs_symlinks
def test_folders_the_selection_keeps_stay_when_a_link_inside_is_dropped(
    tmp_path: Path,
) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(_entry("a/", mode=0o40755), b"")
        zf.writestr(_entry("a/b/", mode=0o40755), b"")
        zf.writestr(_entry("a/b/link", mode=0o120777), b"../../x")
        zf.writestr(_entry("x", mode=0o100644), b"X")
    results, tree = _both(
        buf.getvalue(),
        tmp_path,
        members=lambda member: member.type is not MemberType.SYMLINK,
    )
    assert [name for name, _, _ in results] == ["a/", "a/b/", "x"]
    assert sorted(tree) == ["a", "a/b", "x"]


@posix_modes
def test_a_folder_the_list_keeps_stays_when_a_member_inside_is_dropped(
    tmp_path: Path,
) -> None:
    data = _zip([(_entry("d/", mode=0o40755), b""), (_entry("d\\x"), b"1")])
    results, tree = _both(data, tmp_path, members=["d/", "d/x"])
    assert [name for name, _, _ in results] == ["d/"]
    assert sorted(tree) == ["d"]


@needs_symlinks
def test_a_member_selected_in_the_end_reports_progress(tmp_path: Path) -> None:
    ticks: dict[str, list[tuple[str, int]]] = {}
    for where, piped in (("file", False), ("pipe", True)):
        seen = ticks[where] = []
        _extract(
            _link_zip("a.txt"),
            tmp_path / where,
            piped=piped,
            members=lambda member: member.type is MemberType.SYMLINK,
            on_progress=lambda p, seen=seen: seen.append(
                (p.member.name, p.members_done)
            ),
        )
    assert ticks["pipe"] == ticks["file"] == [("link", 1)]


@needs_symlinks
def test_a_member_the_filter_drops_in_the_end_reports_progress(tmp_path: Path) -> None:
    ticks: dict[str, list[tuple[str, int]]] = {}
    for where, piped in (("file", False), ("pipe", True)):
        seen = ticks[where] = []
        _extract(
            _link_zip("a.txt"),
            tmp_path / where,
            piped=piped,
            members=lambda member: member.type is MemberType.SYMLINK,
            filter=lambda m: None if m.type is MemberType.SYMLINK else m,
            on_progress=lambda p, seen=seen: seen.append(
                (p.member.name, p.members_done)
            ),
        )
    assert ticks["pipe"] == ticks["file"] == [("link", 1)]


@posix_modes
def test_the_filter_is_told_when_the_member_can_still_change(tmp_path: Path) -> None:
    """``member_state_final`` on what the filter gets, as on what a predicate gets."""
    data = _zip([(_entry("a\\"), b"")])
    seen: list[tuple[str, bool]] = []

    def probe(member: ArchiveMember) -> ArchiveMember:
        seen.append((member.name, member.member_state_final))
        return member

    _extract(data, tmp_path, piped=True, filter=probe, on_error="continue")
    assert seen[-1][1] is True
    assert all(final is False for _, final in seen[:-1])
    assert len(seen) == 2


def test_a_special_file_the_directory_reveals_is_removed(tmp_path: Path) -> None:
    data = _zip([(_entry("fifo", mode=0o010644), b""), (_entry("b"), b"after")])
    results, tree = _both(data, tmp_path, on_error="continue")
    assert results[0][:2] == ("fifo", ExtractionStatus.BLOCKED)
    assert sorted(tree) == ["b"]


@pytest.mark.parametrize("policy", list(ExtractionPolicy))
def test_a_dos_name_moves_to_its_folder(
    policy: ExtractionPolicy, tmp_path: Path
) -> None:
    """A DOS host makes ``\\`` a separator, which only the directory says."""
    data = _zip(
        [
            (_entry("docs\\", host=0), b""),
            (_entry("docs\\readme.txt", host=0), b"read me"),
        ]
    )
    results, tree = _both(data, tmp_path, policy=policy)
    assert [name for name, _, _ in results] == ["docs/", "docs/readme.txt"]
    assert tree["docs/readme.txt"][0] == b"read me"


def test_a_rewrite_from_disk_is_counted_as_a_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The renamed file is written again from the bytes already on disk, which the
    byte cap counts and the decompression ratio does not: nothing is decoded twice."""
    counted: dict[str, int] = {"decoded": 0, "copied": 0}
    count, count_copy = BombTracker.count, BombTracker.count_copy

    def decoded(self: BombTracker, n: int) -> None:
        counted["decoded"] += n
        count(self, n)

    def copied(self: BombTracker, n: int, *, at_link_limit: bool) -> None:
        counted["copied"] += n
        count_copy(self, n, at_link_limit=at_link_limit)

    monkeypatch.setattr(BombTracker, "count", decoded)
    monkeypatch.setattr(BombTracker, "count_copy", copied)
    data = _zip([(_entry("docs\\readme.txt", host=0), b"read me")])
    _extract(data, tmp_path / "pipe", piped=True)
    # On Windows the pass already writes it at docs/readme.txt (CI), so nothing is
    # written again.
    assert counted == {"decoded": 7, "copied": 0 if os.name == "nt" else 7}


@pytest.mark.parametrize("policy", list(ExtractionPolicy))
def test_a_unix_name_ending_in_a_backslash_is_a_file(
    policy: ExtractionPolicy, tmp_path: Path
) -> None:
    """The pass takes a trailing ``\\`` for a folder until the directory names the
    host; a Unix host makes it a file, empty or not."""
    data = _zip([(_entry("odd\\", mode=0o100644), b""), (_entry("b"), b"after")])
    results, _ = _both(data, tmp_path, policy=policy, on_error="continue")
    assert [status for _, status, _ in results] == [ExtractionStatus.EXTRACTED] * 2


@posix_modes
def test_a_unix_file_ending_in_a_backslash_with_data_fails_from_a_pipe(
    tmp_path: Path,
) -> None:
    """Its bytes went by while the pass took it for a folder (a crafted shape)."""
    data = _zip([(_entry("odd\\", mode=0o100644), b"data")])
    assert _extract(data, tmp_path / "file", piped=False, policy="trusted") == [
        ("odd\\", ExtractionStatus.EXTRACTED, "odd\\")
    ]
    piped = _extract(
        data, tmp_path / "pipe", piped=True, policy="trusted", on_error="continue"
    )
    assert piped == [("odd\\", ExtractionStatus.FAILED, None)]
    assert list((tmp_path / "pipe").iterdir()) == []


def test_a_reparse_member_whose_data_is_no_link_stays_a_file(tmp_path: Path) -> None:
    content = b"not a reparse buffer " * 10
    _, tree = _both(_reparse_zip(content), tmp_path)
    assert tree["tree/entry"][0] == content


@needs_symlinks
def test_a_reparse_member_holding_a_link_buffer_becomes_a_link(tmp_path: Path) -> None:
    from archivey.internal.windows_reparse import IO_REPARSE_TAG_SYMLINK
    from tests.test_windows_reparse import _reparse_buffer

    buffer = _reparse_buffer(IO_REPARSE_TAG_SYMLINK, "after.txt", "after.txt")
    _, tree = _both(_reparse_zip(buffer), tmp_path)
    assert tree["tree/entry"] == ("symlink", "after.txt")


def _corpus_zips() -> list[object]:
    return [
        pytest.param(entry, id=entry.id)
        for entry in CORPUS
        if "zip" in entry.formats and not entry.passwords
    ]


@pytest.mark.filterwarnings("ignore:Duplicate name")
@pytest.mark.parametrize("entry", _corpus_zips())
def test_the_corpus_extracts_from_a_pipe_as_from_a_file(
    entry: object, tmp_path: Path
) -> None:
    skip_unless_runnable(entry, "zip")  # type: ignore[arg-type]
    path = tmp_path / "corpus.zip"
    build_archive(entry, "zip", path)  # type: ignore[arg-type]
    _both(path.read_bytes(), tmp_path, on_error=OnError.CONTINUE)


# -- Failures found at the end of the pass (design.md, same heading) -----------------


def _piped_report(
    data: bytes, dest: Path, **options: object
) -> archivey.ExtractionReport:
    with archivey.open_archive(NonSeekableBytesIO(data), streaming=True) as reader:
        return reader.extract_all(dest, **options)  # type: ignore[arg-type]


def _outcomes(
    report: archivey.ExtractionReport,
) -> list[tuple[str, ExtractionStatus, type[BaseException] | None]]:
    return [
        (r.member.name, r.status, None if r.error is None else type(r.error))
        for r in report.results
    ]


def test_an_unlisted_local_entry_is_taken_back(tmp_path: Path) -> None:
    hand = _Hand()
    hand.local(b"a.txt", b"one")
    hand.local(b"sub/planted.txt", b"not listed")
    hand.local(b"b.txt", b"two")
    results, tree = _both(hand.directory(0, 2), tmp_path)
    assert [name for name, _, _ in results] == ["a.txt", "b.txt"]
    assert sorted(tree) == ["a.txt", "b.txt"]


def _reversed_pair() -> bytes:
    hand = _Hand()
    hand.local(b"dup.txt", b"first")
    hand.local(b"dup.txt", b"second")
    hand.local(b"other.txt", b"other")
    return hand.directory(1, 0, 2)


def test_a_reversed_same_name_pair_fails_the_current_copy(tmp_path: Path) -> None:
    """The pass wrote the second copy; the directory makes the first current, whose
    bytes have gone by. Random access writes "first" there."""
    data = _reversed_pair()
    report = _piped_report(data, tmp_path / "pipe", on_error=OnError.CONTINUE)
    assert _outcomes(report) == [
        ("dup.txt", ExtractionStatus.FAILED, CorruptionError),
        ("dup.txt", ExtractionStatus.SUPERSEDED, None),
        ("other.txt", ExtractionStatus.EXTRACTED, None),
    ]
    assert sorted(p.name for p in (tmp_path / "pipe").iterdir()) == ["other.txt"]
    _extract(data, tmp_path / "file", piped=False)
    assert (tmp_path / "file" / "dup.txt").read_bytes() == b"first"


def test_a_reversed_pair_under_stop_raises_after_settling(tmp_path: Path) -> None:
    with pytest.raises(CorruptionError, match="current copy") as raised:
        _piped_report(_reversed_pair(), tmp_path, on_error=OnError.STOP)
    assert raised.value.member_name == "dup.txt"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["other.txt"]


def test_an_empty_current_copy_is_written_again(tmp_path: Path) -> None:
    """A copy that needs no bytes can be written at the end."""
    hand = _Hand()
    hand.local(b"dup.txt", b"")
    hand.local(b"dup.txt", b"second")
    report = _piped_report(hand.directory(1, 0), tmp_path)
    # In the order the pass met them; random access lists the directory's order.
    assert _outcomes(report) == [
        ("dup.txt", ExtractionStatus.EXTRACTED, None),
        ("dup.txt", ExtractionStatus.SUPERSEDED, None),
    ]
    assert (tmp_path / "dup.txt").read_bytes() == b""


def test_a_contradicted_descriptor_fails_the_member(tmp_path: Path) -> None:
    data, _whole = _planted_prefix_zip()
    report = _piped_report(data, tmp_path, on_error=OnError.CONTINUE)
    assert _outcomes(report) == [
        ("outer.bin", ExtractionStatus.FAILED, CorruptionError),
        ("after.txt", ExtractionStatus.EXTRACTED, None),
    ]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["after.txt"]


def test_a_contradicted_descriptor_under_stop_raises(tmp_path: Path) -> None:
    data, _whole = _planted_prefix_zip()
    # The planted member's data fails its check as the pass writes it, which would
    # stop the run first (a failure during the pass stops it before the directory
    # can say the member is not listed), so the filter leaves it out.
    with pytest.raises(CorruptionError, match="data descriptor") as raised:
        _piped_report(
            data,
            tmp_path,
            on_error=OnError.STOP,
            filter=lambda m: None if m.name == "inner" else m,
        )
    assert raised.value.member_name == "outer.bin"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["after.txt"]


def _contradicted_header() -> bytes:
    """``a.txt`` and ``b.txt``, with ``a.txt``'s central CRC contradicting the CRC its
    local header holds and its data matches."""
    hand = _Hand()
    hand.local(b"a.txt", b"one")
    hand.local(b"b.txt", b"two")
    data = bytearray(hand.directory(0, 1))
    struct.pack_into("<I", data, data.index(b"PK\x01\x02") + 16, 0x1234_5678)
    return bytes(data)


def test_a_contradicted_local_header_fails_the_member(tmp_path: Path) -> None:
    report = _piped_report(_contradicted_header(), tmp_path, on_error=OnError.CONTINUE)
    assert _outcomes(report) == [
        ("a.txt", ExtractionStatus.FAILED, CorruptionError),
        ("b.txt", ExtractionStatus.EXTRACTED, None),
    ]
    # The pass wrote a.txt's bytes before the directory contradicted its header.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.txt"]


def test_a_contradicted_local_header_under_stop_raises(tmp_path: Path) -> None:
    with pytest.raises(CorruptionError, match="local header and its central") as raised:
        _piped_report(_contradicted_header(), tmp_path, on_error=OnError.STOP)
    assert raised.value.member_name == "a.txt"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.txt"]


def _missing_entry(name: bytes = b"b.txt") -> bytes:
    """``a.txt`` and ``b.txt``, with ``b.txt``'s directory entry pointing past the
    stream's last local header, and named ``name`` (five bytes) there."""
    hand = _Hand()
    hand.local(b"a.txt", b"one")
    hand.local(b"b.txt", b"two")
    data = bytearray(hand.directory(0, 1))
    cd = data.rindex(b"PK\x01\x02")
    struct.pack_into("<I", data, cd + 42, 999_999)
    assert len(name) == 5
    data[cd + 46 : cd + 51] = name
    return bytes(data)


def test_a_directory_entry_with_no_local_entry_gets_a_failed_result(
    tmp_path: Path,
) -> None:
    report = _piped_report(_missing_entry(), tmp_path, on_error=OnError.CONTINUE)
    assert _outcomes(report) == [
        ("a.txt", ExtractionStatus.EXTRACTED, None),
        ("b.txt", ExtractionStatus.FAILED, CorruptionError),
    ]
    # b.txt's local entry is one the directory does not list, so it is taken back.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]


def test_a_name_the_two_headers_disagree_on_fails_the_member(tmp_path: Path) -> None:
    hand = _Hand()
    hand.local(b"a.txt", b"one")
    hand.local(b"b.txt", b"two")
    data = bytearray(hand.directory(0, 1))
    cd = data.rindex(b"PK\x01\x02")
    data[cd + 46 : cd + 51] = b"z.txt"
    report = _piped_report(bytes(data), tmp_path, on_error=OnError.CONTINUE)
    assert _outcomes(report) == [
        ("a.txt", ExtractionStatus.EXTRACTED, None),
        ("b.txt", ExtractionStatus.FAILED, CorruptionError),
    ]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]


def test_a_directory_entry_with_no_local_entry_under_stop_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(CorruptionError) as raised:
        _piped_report(_missing_entry(), tmp_path, on_error=OnError.STOP)
    assert raised.value.member_name == "b.txt"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]
    # The raise is the record: no "Skipping" line beside it.
    assert not [r for r in caplog.records if "Skipping" in r.getMessage()]


def test_a_directory_entry_with_no_local_entry_is_reported_to_on_progress(
    tmp_path: Path,
) -> None:
    seen: list[archivey.ExtractionProgress] = []
    report = _piped_report(
        _missing_entry(), tmp_path, on_error=OnError.CONTINUE, on_progress=seen.append
    )
    failed = report.results[-1].member
    assert report.results[-1].status is ExtractionStatus.FAILED
    assert seen[-1].member is failed
    # One past the pass's count, which already took in the unlisted local copy.
    assert seen[-1].members_done == seen[-2].members_done + 1


def test_a_directory_entry_with_no_local_entry_can_be_blocked(tmp_path: Path) -> None:
    """The policy checks run on it as on any member, and AbortOn applies."""
    report = _piped_report(_missing_entry(b"../.."), tmp_path / "a")
    assert _outcomes(report)[-1][:2] == ("../..", ExtractionStatus.BLOCKED)
    with pytest.raises(FilterRejectionError):
        _piped_report(
            _missing_entry(b"../.."),
            tmp_path / "b",
            abort_on={AbortOn.BLOCKED_MEMBER},
        )


@pytest.mark.skipif(os.name == "nt", reason="'?' is no name character on Windows")
def test_a_directory_entry_with_no_local_entry_records_its_rewrite(
    tmp_path: Path,
) -> None:
    report = _piped_report(
        _missing_entry(b"a?.tx"),
        tmp_path,
        on_error=OnError.CONTINUE,
        policy=ExtractionPolicy.STANDARD,
    )
    result = report.results[-1]
    assert result.status is ExtractionStatus.FAILED
    assert result.presented_name is not None
    assert NameRewrite.PORTABLE_NAME in result.rewrites


def test_a_name_the_two_headers_disagree_on_under_stop_raises(tmp_path: Path) -> None:
    hand = _Hand()
    hand.local(b"a.txt", b"one")
    hand.local(b"b.txt", b"two")
    data = bytearray(hand.directory(0, 1))
    cd = data.rindex(b"PK\x01\x02")
    data[cd + 46 : cd + 51] = b"z.txt"
    with pytest.raises(CorruptionError, match="differ") as raised:
        _piped_report(bytes(data), tmp_path, on_error=OnError.STOP)
    # The name the pass yielded it under, which the report would carry.
    assert raised.value.member_name == "b.txt"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]


def test_a_reversed_pair_under_skip_matches_the_seekable_results(
    tmp_path: Path,
) -> None:
    """Neither copy is written over the caller's file, so neither needs the bytes the
    pass has gone by: each copy gets the result random access gives it."""
    data = _reversed_pair()
    outcomes = {}
    for piped in (True, False):
        dest = tmp_path / ("pipe" if piped else "file")
        dest.mkdir()
        (dest / "dup.txt").write_bytes(b"caller's")
        source = NonSeekableBytesIO(data) if piped else io.BytesIO(data)
        with archivey.open_archive(source, streaming=piped) as reader:
            report = reader.extract_all(
                dest, overwrite=OverwritePolicy.SKIP, on_error=OnError.CONTINUE
            )
        outcomes[piped] = sorted(
            (r.member._member_id is not None and r.member.is_current, r.status.value)
            for r in report.results
            if r.member.name == "dup.txt"
        )
        assert (dest / "dup.txt").read_bytes() == b"caller's"
    assert (
        outcomes[True]
        == outcomes[False]
        == [
            (False, "superseded"),
            (True, "not_overwritten"),
        ]
    )


def _failing_filter(member: ArchiveMember) -> ArchiveMember:
    raise ExtractionError("refused by the test", member_name=member.name)


@pytest.mark.parametrize(
    ("options", "status"),
    [
        pytest.param({}, "blocked", id="blocked"),
        pytest.param({"filter": _failing_filter}, "failed", id="filter-fails"),
    ],
)
def test_a_reversed_pair_refused_before_the_write_matches_the_seekable_results(
    tmp_path: Path, options: dict[str, object], status: str
) -> None:
    """A refusal made before the write stands on a copy the directory makes not
    current: random access runs the filter and the policy checks on every copy."""
    hand = _Hand()
    hand.local(b"../dup.txt", b"first")
    hand.local(b"../dup.txt", b"second")
    data = hand.directory(1, 0)
    outcomes = {}
    for piped in (True, False):
        dest = tmp_path / ("pipe" if piped else "file")
        source = NonSeekableBytesIO(data) if piped else io.BytesIO(data)
        with archivey.open_archive(source, streaming=piped) as reader:
            report = reader.extract_all(dest, on_error=OnError.CONTINUE, **options)  # type: ignore[arg-type]
        outcomes[piped] = sorted(
            (r.member.is_current, r.status.value) for r in report.results
        )
    assert outcomes[True] == outcomes[False] == [(False, status), (True, status)]


def _compare_with_file_read(
    data: bytes, tmp_path: Path, *, seed: bytes | None = None, **options: object
) -> dict[bool, tuple[list[tuple[str, bool, str, str]], dict[str, bytes]]]:
    """Each run's (name, is_current, status, error type) results, sorted, and what it
    left on disk, for a pipe read (``True``) and a file read (``False``) of ``data``.
    ``seed`` is put at ``b.txt`` in each destination first."""
    runs = {}
    for piped in (True, False):
        dest = tmp_path / ("pipe" if piped else "file")
        dest.mkdir()
        if seed is not None:
            (dest / "b.txt").write_bytes(seed)
        source = NonSeekableBytesIO(data) if piped else io.BytesIO(data)
        with archivey.open_archive(source, streaming=piped) as reader:
            report = reader.extract_all(dest, on_error=OnError.CONTINUE, **options)  # type: ignore[arg-type]
        runs[piped] = (
            sorted(
                (
                    r.member.name,
                    r.member.is_current,
                    r.status.value,
                    type(r.error).__name__,
                )
                for r in report.results
            ),
            {p.name: p.read_bytes() for p in dest.iterdir()},
        )
    return runs


def test_a_missing_entry_makes_the_copy_it_follows_not_current(tmp_path: Path) -> None:
    hand = _Hand()
    hand.local(b"a.txt", b"one")
    hand.local(b"b.txt", b"two")
    data = bytearray(hand.directory(0, 1))
    # The second entry names a.txt and points where the stream held nothing.
    cd = data.rindex(b"PK\x01\x02")
    struct.pack_into("<I", data, cd + 42, 999_999)
    data[cd + 46 : cd + 51] = b"a.txt"
    runs = _compare_with_file_read(bytes(data), tmp_path)
    assert (
        runs[True]
        == runs[False]
        == (
            [
                ("a.txt", False, "superseded", "NoneType"),
                ("a.txt", True, "failed", "CorruptionError"),
            ],
            {},
        )
    )


@pytest.mark.parametrize(
    ("overwrite", "outcome"),
    [
        (OverwritePolicy.SKIP, ("not_overwritten", "NoneType")),
        (OverwritePolicy.ERROR, ("failed", "ExtractionError")),
    ],
)
def test_a_missing_entry_meets_the_overwrite_policy_first(
    tmp_path: Path, overwrite: OverwritePolicy, outcome: tuple[str, str]
) -> None:
    runs = _compare_with_file_read(
        _missing_entry(), tmp_path, seed=b"caller's", overwrite=overwrite
    )
    assert (
        runs[True]
        == runs[False]
        == (
            [("a.txt", True, "extracted", "NoneType"), ("b.txt", True, *outcome)],
            {"a.txt": b"one", "b.txt": b"caller's"},
        )
    )


def test_a_moved_copy_the_filesystem_keeps_is_logged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A DOS name is written again under its real name from the copy the pass wrote,
    moved aside first; a removal of that copy the filesystem refuses is reported."""
    fixture = Path(__file__).parent / "fixtures" / "zip_backslash" / "dos_backslash.zip"
    real_unlink = os.unlink

    def refuse_temp(path: str | os.PathLike[str], *args: object, **kw: object) -> None:
        if os.path.basename(path).startswith(".archivey-tmp-"):
            raise PermissionError(13, "Permission denied", str(path))
        real_unlink(path, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "unlink", refuse_temp)
    with archivey.open_archive(
        NonSeekableBytesIO(fixture.read_bytes()), streaming=True
    ) as reader:
        reader.extract_all(tmp_path / "out")
    assert "the earlier copy of" in caplog.text
