"""Windows reparse points in a ZIP read from a pipe, against the same archive from a file.

Only the central directory, after the last member, says a member is a reparse point,
so the pass keeps the start of data over the link target cap as far as a reparse
buffer's parse reads it, whether or not the caller read the member.
"""

from __future__ import annotations

import io
import struct
import sys
import zipfile
from pathlib import Path

import pytest

import archivey
from archivey import ExtractionStatus
from archivey.config import ListingLimits
from archivey.diagnostics import DiagnosticCode
from archivey.internal.backends.zip_forward_reader import _MAX_REPARSE_PREFIX
from archivey.internal.base_reader import MAX_LINK_TARGET_BYTES
from archivey.internal.windows_reparse import (
    FILE_ATTRIBUTE_REPARSE_POINT,
    IO_REPARSE_TAG_MOUNT_POINT,
    IO_REPARSE_TAG_SYMLINK,
)
from archivey.types import ArchiveMember, MemberType
from tests.memory_util import traced_peak
from tests.streams_util import NonSeekableBytesIO
from tests.test_windows_reparse import _reparse_buffer
from tests.zipcrypto import build_zipcrypto_zip

_FILE_ATTRIBUTE_DIRECTORY = 0x10


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


def test_a_member_declaring_a_huge_link_buffer_costs_the_pass_nothing() -> None:
    """Data over the cap that opens like a symlink buffer declaring 64 KiB keeps
    nothing: no target within the cap needs that much, and the directory never said
    these are reparse points. Kept, 40 of them would pass this listing limit."""
    content = struct.pack("<IHH", IO_REPARSE_TAG_SYMLINK, 0xFFFF, 0) + bytes(70_000)
    data = _zip([(f"{i}.bin", 0x20, content) for i in range(40)])
    config = archivey.ArchiveyConfig(
        listing_limits=ListingLimits(max_metadata_bytes=1 << 20)
    )
    with archivey.open_archive(io.BytesIO(data), config=config) as reader:
        from_file = [m.name for m in reader.members()]
    with archivey.open_archive(
        NonSeekableBytesIO(data), streaming=True, config=config
    ) as reader:
        for _ in reader.stream_members():
            pass
        piped = [m.name for m in reader.members_report().members]
    assert piped == from_file


def _target_messages(source: io.BytesIO, *, streaming: bool) -> list[str]:
    with archivey.open_archive(source, streaming=streaming) as reader:
        if streaming:
            for _ in reader.stream_members():
                pass
        else:
            reader.members()
        return [
            d.message
            for d in reader.diagnostics.retained
            if d.code is DiagnosticCode.SYMLINK_TARGET_UNAVAILABLE
        ]


def test_a_damaged_reparse_buffer_over_the_cap_says_why_as_from_a_file() -> None:
    buffer = _reparse_buffer(IO_REPARSE_TAG_SYMLINK, "t" * 1990, "t" * 1990)
    assert len(buffer) > MAX_LINK_TARGET_BYTES
    raw = bytearray(_zip([("link", 0x20 | FILE_ATTRIBUTE_REPARSE_POINT, buffer)]))
    name_len, extra_len = struct.unpack_from("<HH", raw, 26)
    start = 30 + name_len + extra_len
    raw[start + 2] ^= 0xFF
    raw[start + 3] ^= 0xFF
    from_file = _target_messages(io.BytesIO(bytes(raw)), streaming=False)
    piped = _target_messages(NonSeekableBytesIO(bytes(raw)), streaming=True)
    assert len(piped) == len(from_file) == 1
    assert "over 4096 bytes" not in piped[0]
    assert "deflate" in piped[0] and "deflate" in from_file[0]


def test_parent_folders_of_a_file_the_pass_made_a_refused_link_are_taken_back(
    tmp_path: Path,
) -> None:
    """The pass writes ``d/f`` as a file; the directory makes it a link to an
    absolute target, which is refused, and ``d`` goes with it, as the file read never
    makes it."""
    buffer = _reparse_buffer(
        IO_REPARSE_TAG_SYMLINK, "C:\\tree\\target", "C:\\tree\\target"
    )
    data = _zip([("d/f", 0x20 | FILE_ATTRIBUTE_REPARSE_POINT, buffer)])
    trees = []
    for piped in (False, True):
        out = tmp_path / ("pipe" if piped else "file")
        source = NonSeekableBytesIO(data) if piped else io.BytesIO(data)
        with archivey.open_archive(source, streaming=piped) as reader:
            report = reader.extract_all(out)
        assert [r.status for r in report.results] == [ExtractionStatus.BLOCKED]
        trees.append(sorted(p.relative_to(out) for p in out.rglob("*")))
    assert trees == [[], []]


def _extracted(
    data: bytes, out: Path, *, piped: bool, config: archivey.ArchiveyConfig
) -> tuple[list[ExtractionStatus], list[tuple[Path, bool]]]:
    source = NonSeekableBytesIO(data) if piped else io.BytesIO(data)
    with archivey.open_archive(source, streaming=piped, config=config) as reader:
        report = reader.extract_all(out)
    tree = sorted((p.relative_to(out), p.is_symlink()) for p in out.rglob("*"))
    return [r.status for r in report.results], tree


@pytest.mark.parametrize("read_link_targets", [True, False])
def test_a_reparse_link_over_the_cap_is_written_from_a_pipe_as_from_a_file(
    tmp_path: Path, read_link_targets: bool
) -> None:
    """Nobody reads a folder-named member's data, so the pass keeps its buffer's
    start while draining it, whatever ``read_link_targets`` says: a file read still
    reads the target extraction asks for. The long name is the substitute one, so
    the target written stays within what macOS takes."""
    buffer = _reparse_buffer(IO_REPARSE_TAG_SYMLINK, "t" * 2100, "target")
    assert len(buffer) > MAX_LINK_TARGET_BYTES
    attributes = _FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT
    data = _zip([("linkdir/", attributes, buffer)])
    config = archivey.ArchiveyConfig(read_link_targets=read_link_targets)
    from_file = _extracted(data, tmp_path / "file", piped=False, config=config)
    piped = _extracted(data, tmp_path / "pipe", piped=True, config=config)
    assert piped == from_file
    if not sys.platform.startswith("win"):
        assert from_file == ([ExtractionStatus.EXTRACTED], [(Path("linkdir"), True)])


def _padded_link() -> bytes:
    """A symlink buffer whose payload declares 20 000 bytes of padding past its names:
    longer than Windows writes, and well formed."""
    buffer = _reparse_buffer(IO_REPARSE_TAG_SYMLINK, "tree\\target", "tree\\target")
    tag, length, reserved = struct.unpack_from("<IHH", buffer)
    padded = struct.pack("<IHH", tag, length + 20_000, reserved) + buffer[8:]
    return padded + bytes(20_000)


def test_a_link_buffer_padded_past_the_bound_is_read_only_from_a_file() -> None:
    """The stated exception: the pass keeps no buffer over the bound, so the target a
    file read reports is unavailable from a pipe, and says why."""
    content = _padded_link()
    assert len(content) > _MAX_REPARSE_PREFIX
    data = _zip([("link", 0x20 | FILE_ATTRIBUTE_REPARSE_POINT, content)])
    assert _from_file(data)["link"].link_target == "tree/target"
    assert _target_messages(io.BytesIO(data), streaming=False) == []
    assert _from_pipe(data, read=False)["link"].link_target is None
    (message,) = _target_messages(NonSeekableBytesIO(data), streaming=True)
    assert f"reparse buffer of {len(content)} bytes" in message
    assert f"over the {_MAX_REPARSE_PREFIX}" in message


def test_members_refused_a_reparse_prefix_keep_no_message_while_the_pass_runs() -> None:
    """A member whose data declares a buffer over the bound costs the pass what one
    declaring none does; the message is built only for a link."""

    def archive(tag: int) -> bytes:
        content = struct.pack("<IHH", tag, 0xFFFF, 0) + bytes(4100)
        return _zip([(f"{i}.bin", 0x20, content) for i in range(3000)])

    def listing(data: bytes) -> None:
        with archivey.open_archive(NonSeekableBytesIO(data), streaming=True) as reader:
            for _ in reader.stream_members():
                pass

    refused, control = archive(IO_REPARSE_TAG_SYMLINK), archive(0x80000013)
    listing(control)
    refused_peak = traced_peak(lambda: listing(refused))
    control_peak = traced_peak(lambda: listing(control))
    assert refused_peak < control_peak + 3000 * 100


def _long_link_buffer() -> bytes:
    """A symlink buffer over the target cap whose target is short."""
    buffer = _reparse_buffer(IO_REPARSE_TAG_SYMLINK, "t" * 2100, "target")
    assert len(buffer) > MAX_LINK_TARGET_BYTES
    return buffer


def test_a_reparse_buffer_that_is_all_the_data_has_its_crc_checked(
    tmp_path: Path,
) -> None:
    """The file read reads one byte past the buffer, reaches the data's end and checks
    it; so does the pass, and a damaged one is no link from either."""
    attributes = _FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT
    raw = bytearray(_zip([("linkdir/", attributes, _long_link_buffer())]))
    central = raw.rindex(b"PK\x01\x02")
    for crc_at in (14, central + 16):
        raw[crc_at] ^= 0xFF
    data = bytes(raw)
    from_file = _from_file(data)["linkdir"]
    piped = _from_pipe(data, read=False)["linkdir"]
    assert from_file.link_target is piped.link_target is None
    assert len(_target_messages(io.BytesIO(data), streaming=False)) == 1
    (message,) = _target_messages(NonSeekableBytesIO(data), streaming=True)
    assert "CRC" in message
    results = []
    for streaming in (False, True):
        source = NonSeekableBytesIO(data) if streaming else io.BytesIO(data)
        with archivey.open_archive(source, streaming=streaming) as reader:
            report = reader.extract_all(tmp_path / str(streaming), on_error="continue")
        results.append([r.status for r in report.results])
    assert results == [[ExtractionStatus.FAILED]] * 2


def test_an_unread_encrypted_reparse_buffer_over_the_cap_says_why_from_a_pipe() -> None:
    """The stated exception: the pass derives no key for a member nobody read and
    keeps no ciphertext of data that decodes past the cap, so the target a file read
    reports is unavailable from a pipe, and the reason says the pass did not decrypt
    it."""
    raw = bytearray(build_zipcrypto_zip(b"secret", b"linkdir/", _long_link_buffer()))
    central = raw.rindex(b"PK\x01\x02")
    attributes = _FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT
    struct.pack_into("<I", raw, central + 38, attributes)
    data = bytes(raw)
    with archivey.open_archive(io.BytesIO(data), password="secret") as reader:
        assert reader.members()[0].link_target == "target"
    with archivey.open_archive(
        NonSeekableBytesIO(data), streaming=True, password="secret"
    ) as reader:
        for _ in reader.stream_members():
            pass
        assert reader.members_report().members[0].link_target is None
        (message,) = [
            d.message
            for d in reader.diagnostics.retained
            if d.code is DiagnosticCode.SYMLINK_TARGET_UNAVAILABLE
        ]
    assert "did not decrypt" in message
    assert "decodes to more than the 4096-byte symlink target cap" in message


def test_a_reparse_buffer_the_caller_read_to_a_failed_crc_is_no_link() -> None:
    raw = bytearray(
        _zip([("link", 0x20 | FILE_ATTRIBUTE_REPARSE_POINT, _long_link_buffer())])
    )
    central = raw.rindex(b"PK\x01\x02")
    for crc_at in (14, central + 16):
        raw[crc_at] ^= 0xFF
    with archivey.open_archive(
        NonSeekableBytesIO(bytes(raw)), streaming=True
    ) as reader:
        for _, stream in reader.stream_members():
            assert stream is not None
            with pytest.raises(archivey.CorruptionError):
                stream.read()
        assert reader.members_report().members[0].link_target is None
