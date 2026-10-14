"""Windows reparse points in a ZIP read from a pipe, against the same archive from a file.

Only the central directory, after the last member, says a member is a reparse point,
so the pass keeps the start of data over the link target cap as far as a reparse
buffer's parse reads it, whether or not the caller read the member. ``open_archive``
still refuses a pipe for ZIP until the rest of the change lands, so these tests open
the gate themselves.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

import archivey
from archivey import ExtractionStatus
from archivey.internal.backends.zip_reader import ZipReadBackend
from archivey.internal.base_reader import MAX_LINK_TARGET_BYTES
from archivey.internal.windows_reparse import (
    FILE_ATTRIBUTE_REPARSE_POINT,
    IO_REPARSE_TAG_MOUNT_POINT,
    IO_REPARSE_TAG_SYMLINK,
)
from archivey.types import ArchiveMember, MemberType
from tests.streams_util import NonSeekableBytesIO
from tests.test_windows_reparse import _reparse_buffer

_FILE_ATTRIBUTE_DIRECTORY = 0x10


@pytest.fixture(autouse=True)
def _pipes_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ZipReadBackend, "SUPPORTS_STREAMING_NON_SEEKABLE", True)


def _zip(entries: list[tuple[str, int, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, attributes, data in entries:
            info = zipfile.ZipInfo(name)
            info.create_system = 0
            info.external_attr = attributes
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


def _from_file(data: bytes) -> dict[str, ArchiveMember]:
    with archivey.open_archive(io.BytesIO(data)) as reader:
        return {m.name: m for m in reader.members()}


def _from_pipe(data: bytes, *, read: bool) -> dict[str, ArchiveMember]:
    with archivey.open_archive(NonSeekableBytesIO(data), streaming=True) as reader:
        for _, stream in reader.stream_members():
            if read and stream is not None:
                stream.read()
        return {m.name: m for m in reader.members_report().members}


@pytest.mark.parametrize("read", [True, False], ids=["read", "unread"])
def test_a_reparse_target_at_the_cap_is_kept_from_data_over_it(read: bool) -> None:
    """The UTF-16 buffer of a target at the cap is about four times the cap, so the
    pass keeps the buffer's start past the cap, as far as the buffer declares."""
    at_cap = "a" * MAX_LINK_TARGET_BYTES
    over = "b" * (MAX_LINK_TARGET_BYTES + 1)
    attributes = 0x20 | FILE_ATTRIBUTE_REPARSE_POINT
    data = _zip(
        [
            (
                "at_cap",
                attributes,
                _reparse_buffer(IO_REPARSE_TAG_SYMLINK, at_cap, at_cap),
            ),
            ("over", attributes, _reparse_buffer(IO_REPARSE_TAG_SYMLINK, over, over)),
        ]
    )
    piped = _from_pipe(data, read=read)
    from_file = _from_file(data)
    for name in ("at_cap", "over"):
        assert piped[name].type is from_file[name].type is MemberType.SYMLINK
        assert piped[name].link_target == from_file[name].link_target
    assert piped["at_cap"].link_target == at_cap
    assert piped["over"].link_target is None


@pytest.mark.parametrize("read", [True, False], ids=["read", "unread"])
def test_a_large_reparse_member_with_another_tag_is_a_file(read: bool) -> None:
    """A tag that names no link (a deduplication stub, a cloud placeholder) makes the
    member a file again, whatever its length: its first bytes say so."""
    content = b"\x13\x00\x00\x80" + bytes(4) + b"content" * 1000
    data = _zip([("stub", 0x20 | FILE_ATTRIBUTE_REPARSE_POINT, content)])
    assert _from_file(data)["stub"].type is MemberType.FILE
    assert _from_pipe(data, read=read)["stub"].type is MemberType.FILE


def test_a_link_buffer_with_padding_past_it_keeps_its_target() -> None:
    """Bytes after the declared buffer change no parse, and are not kept."""
    buffer = _reparse_buffer(IO_REPARSE_TAG_SYMLINK, "C:\\target", "C:\\target")
    data = _zip(
        [("link", 0x20 | FILE_ATTRIBUTE_REPARSE_POINT, buffer + bytes(1 << 20))]
    )
    assert _from_file(data)["link"].link_target == "C:/target"
    assert _from_pipe(data, read=False)["link"].link_target == "C:/target"


def test_a_folder_the_pass_made_for_a_junction_is_taken_back(tmp_path: Path) -> None:
    """The name makes the member a folder until the directory says it is a junction;
    the pass writes the folder and its parent, and takes both back when the junction
    is refused, as the file read never writes them."""
    buffer = _reparse_buffer(
        IO_REPARSE_TAG_MOUNT_POINT, "\\??\\C:\\tree\\target", "C:\\tree\\target"
    )
    attributes = _FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT
    data = _zip([("tree/junction_dir/", attributes, buffer)])
    trees = []
    for piped in (False, True):
        out = tmp_path / ("pipe" if piped else "file")
        source = NonSeekableBytesIO(data) if piped else io.BytesIO(data)
        with archivey.open_archive(source, streaming=piped) as reader:
            report = reader.extract_all(out)
        assert [r.status for r in report.results] == [ExtractionStatus.BLOCKED]
        trees.append(sorted(p.relative_to(out) for p in out.rglob("*")))
    assert trees == [[], []]
