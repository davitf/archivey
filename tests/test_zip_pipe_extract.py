"""Extracting a ZIP read from a source that cannot seek.

The pass writes each member as its local header describes it; the central directory,
read after the last member, then settles the mode, type and name of what was written
(``ExtractionCoordinator._settle_revised_members``). Each test extracts the same bytes
from a file and from a pipe and expects the same files on disk and the same results.
``open_archive`` still refuses a pipe for ZIP until the rest of the change lands, so
these tests open the gate themselves.
"""

from __future__ import annotations

import io
import os
import stat
import struct
import zipfile
from pathlib import Path

import pytest

import archivey
from archivey.internal.backends.zip_reader import ZipReadBackend
from archivey.internal.extraction import BombTracker
from archivey.types import (
    ArchiveMember,
    ExtractionPolicy,
    ExtractionStatus,
    MemberType,
    OnError,
)
from tests.sample_archives import CORPUS, build_archive, skip_unless_runnable
from tests.streams_util import NonSeekableBytesIO
from tests.test_zip_pipe import _reparse_zip, _streamed

needs_symlinks = pytest.mark.skipif(
    not hasattr(os, "symlink") or os.name == "nt", reason="needs POSIX symlinks"
)
# Also where a backslash in a name is no separator.
posix_modes = pytest.mark.skipif(os.name == "nt", reason="needs POSIX")


@pytest.fixture(autouse=True)
def _pipes_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ZipReadBackend, "SUPPORTS_STREAMING_NON_SEEKABLE", True)


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


def _tree(root: Path) -> dict[str, tuple[object, ...]]:
    """Each entry under ``root``: its bytes, link target or ``dir``, and for a file or
    directory its mode and modification time (a link gets no metadata applied)."""
    tree: dict[str, tuple[object, ...]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            tree[rel] = ("symlink", os.readlink(path))
            continue
        st = path.stat()
        content = "dir" if path.is_dir() else path.read_bytes()
        tree[rel] = (content, stat.S_IMODE(st.st_mode), int(st.st_mtime))
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
    data: bytes, tmp_path: Path, **options: object
) -> tuple[
    list[tuple[str, ExtractionStatus, str | None]], dict[str, tuple[object, ...]]
]:
    """Extract ``data`` from a file and from a pipe; assert both end the same, and
    return the results and the tree."""
    from_file = _extract(data, tmp_path / "file", piped=False, **options)
    from_pipe = _extract(data, tmp_path / "pipe", piped=True, **options)
    tree = _tree(tmp_path / "file")
    assert from_pipe == from_file
    assert _tree(tmp_path / "pipe") == tree
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
