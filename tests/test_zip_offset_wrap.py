"""ZIPs written past 4 GiB without ZIP64, as macOS Archive Utility writes them.

Such a writer stores every offset modulo 2³², the end record's directory offset too.
The archives here are laid out past 4 GiB without 4 GiB of storage: a sparse,
seekable stream holds the headers and short members at their real offsets and reads
zeros everywhere else, and the one large member is stored zeros. Nothing reads that
member except the pipe test, which streams past it.
"""

from __future__ import annotations

import io
import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from archivey import open_archive
from archivey.config import ArchiveyConfig
from archivey.diagnostics import DiagnosticCode, DiagnosticPolicy, OffsetWrapContext
from archivey.exceptions import CorruptionError, DiagnosticRaisedError
from archivey.internal.backends.zip_parser import (
    CentralDirectoryWalk,
    find_end_record,
    local_name_at,
)
from archivey.types import ArchiveFormat

_WRAP = 1 << 32
_DOS_TIME, _DOS_DATE = 0x6000, 0x5A21
# Version made by Unix 2.1 and version needed 2.0, as macOS Archive Utility writes.
_MADE_BY = (3 << 8) | 21
_BIT3 = 0x08


@dataclass(frozen=True)
class _Zeros:
    """A member of ``size`` zero bytes the sparse stream supplies without storing."""

    size: int
    crc: int


# 2³² - 16 zero bytes, so the member after it starts past 4 GiB. Its CRC-32 is fixed
# here because computing it takes a second.
_BIG = _Zeros((1 << 32) - 16, 0xCA72F00B)


class _Sparse(io.RawIOBase):
    """A seekable stream of ``size`` bytes: ``segments`` at their offsets, zeros
    elsewhere."""

    def __init__(self, segments: Sequence[tuple[int, bytes]], size: int) -> None:
        super().__init__()
        self._segments = sorted(segments)
        self._size = size
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}
        self._pos = base[whence] + offset
        return self._pos

    def tell(self, /) -> int:
        return self._pos

    def readinto(self, b) -> int:  # type: ignore[override]  # test double; broad buffer type
        view = memoryview(b).cast("B")
        start = self._pos
        n = max(min(len(view), self._size - start), 0)
        view[:n] = bytes(n)
        for at, data in self._segments:
            lo, hi = max(at, start), min(at + len(data), start + n)
            if lo < hi:
                view[lo - start : hi - start] = data[lo - at : hi - at]
        self._pos += n
        return n


class _ForwardOnly(_Sparse):
    """The same bytes, read forward only, as from a pipe."""

    def seekable(self) -> bool:
        return False

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        raise io.UnsupportedOperation("seek")


@dataclass(frozen=True)
class _Layout:
    segments: list[tuple[int, bytes]]
    size: int
    #: Each member's real local header offset.
    offsets: list[int]
    cd_offset: int


def _layout(
    members: Sequence[tuple[str, bytes | _Zeros]],
    *,
    central_names: dict[str, str] | None = None,
) -> _Layout:
    """A stored ZIP of ``members`` with every stored offset taken modulo 2³².

    Every short member has a data descriptor (bit 3) and a large one has its sizes in
    the local header, as in the real archive: a pipe then skips the large member's
    data instead of searching it for a descriptor.

    ``central_names`` renames entries in the central directory only, so the local
    header at an offset names another member.
    """
    segments: list[tuple[int, bytes]] = []
    central: list[bytes] = []
    offsets: list[int] = []
    pos = 0
    for name, data in members:
        raw = name.encode()
        if isinstance(data, _Zeros):
            size, crc = data.size, data.crc
        else:
            size, crc = len(data), zlib.crc32(data)
        flags = 0 if isinstance(data, _Zeros) else _BIT3
        local = (0, 0, 0) if flags else (crc, size, size)
        header = struct.pack(
            "<4s2B4HL2L2H",
            b"PK\x03\x04",
            20,
            0,
            flags,
            0,
            _DOS_TIME,
            _DOS_DATE,
            *local,
            len(raw),
            0,
        )
        offsets.append(pos)
        segments.append((pos, header + raw))
        pos += len(header) + len(raw)
        if not isinstance(data, _Zeros):
            segments.append((pos, data))
        pos += size
        if flags:
            segments.append((pos, struct.pack("<4s3L", b"PK\x07\x08", crc, size, size)))
            pos += 16
        stored_name = (central_names or {}).get(name, name).encode()
        central.append(
            struct.pack(
                "<4s4B4HL2L5H2L",
                b"PK\x01\x02",
                _MADE_BY & 0xFF,
                _MADE_BY >> 8,
                20,
                0,
                flags,
                0,
                _DOS_TIME,
                _DOS_DATE,
                crc,
                size,
                size,
                len(stored_name),
                0,
                0,
                0,
                0,
                0o100644 << 16,
                offsets[-1] % _WRAP,
            )
            + stored_name
        )
    directory = b"".join(central)
    cd_offset = pos
    end = struct.pack(
        "<4s4H2LH",
        b"PK\x05\x06",
        0,
        0,
        len(central),
        len(central),
        len(directory),
        cd_offset % _WRAP,
        0,
    )
    segments.append((pos, directory + end))
    return _Layout(segments, pos + len(directory) + len(end), offsets, cd_offset)


def _open(layout: _Layout, **kwargs: object):
    return open_archive(
        _Sparse(layout.segments, layout.size),
        format=ArchiveFormat.ZIP,
        **kwargs,  # type: ignore[arg-type]
    )


def _wraps(reader) -> list[OffsetWrapContext]:
    found = [
        d.context
        for d in reader.diagnostics.retained
        if d.code is DiagnosticCode.ARCHIVE_OFFSET_WRAPPED
    ]
    assert all(isinstance(c, OffsetWrapContext) for c in found)
    return found  # type: ignore[return-value]


_PAST_4_GIB = [("big.bin", _BIG), ("b.txt", b"bee " * 8), ("c.txt", b"sea " * 5)]


def test_offsets_wrapped_past_4_gib_are_corrected() -> None:
    layout = _layout(_PAST_4_GIB)
    assert layout.offsets[1] > _WRAP
    with _open(layout) as reader:
        members = reader.members()
        assert [m.name for m in members] == ["big.bin", "b.txt", "c.txt"]
        assert reader.read(members[1]) == b"bee " * 8
        assert reader.read(members[2]) == b"sea " * 5
        wraps = _wraps(reader)
    assert [(c.record, c.member_name, c.member_id) for c in wraps] == [
        ("local_header", "b.txt", 1),
        ("local_header", "c.txt", 2),
        ("central_directory", "", None),
    ]
    assert [(c.stored_offset, c.corrected_offset) for c in wraps] == [
        (layout.offsets[1] - _WRAP, layout.offsets[1]),
        (layout.offsets[2] - _WRAP, layout.offsets[2]),
        (layout.cd_offset - _WRAP, layout.cd_offset),
    ]


def test_extraction_of_the_members_past_4_gib(tmp_path) -> None:
    with _open(_layout(_PAST_4_GIB)) as reader:
        reader.extract_all(tmp_path, members=["b.txt", "c.txt"])
    assert (tmp_path / "b.txt").read_bytes() == b"bee " * 8
    assert (tmp_path / "c.txt").read_bytes() == b"sea " * 5


def test_strict_refuses_a_wrapped_offset() -> None:
    config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.STRICT)
    with pytest.raises(DiagnosticRaisedError) as caught:
        with _open(_layout(_PAST_4_GIB), config=config) as reader:
            reader.members()
    assert caught.value.diagnostic.code is DiagnosticCode.ARCHIVE_OFFSET_WRAPPED


def test_only_the_directory_offset_wrapped() -> None:
    # Every member starts below 4 GiB and the last one ends past it, so only the end
    # record's offset of the directory wraps.
    layout = _layout([("a.txt", b"ay"), ("big.bin", _BIG)])
    assert layout.offsets[1] < _WRAP < layout.cd_offset
    with _open(layout) as reader:
        members = reader.members()
        assert [m.name for m in members] == ["a.txt", "big.bin"]
        assert reader.read(members[0]) == b"ay"
        wraps = _wraps(reader)
    assert [(c.record, c.stored_offset, c.corrected_offset) for c in wraps] == [
        ("central_directory", layout.cd_offset - _WRAP, layout.cd_offset)
    ]


def test_a_header_with_another_name_keeps_the_stored_offset() -> None:
    # The directory names b.txt "x.txt", so the local header at the corrected offset
    # does not match and the stored offset stays: the member lists and its read fails
    # where the stored offset points, inside big.bin's zeros.
    layout = _layout(_PAST_4_GIB, central_names={"b.txt": "x.txt"})
    with _open(layout) as reader:
        members = reader.members()
        assert [m.name for m in members] == ["big.bin", "x.txt", "c.txt"]
        with pytest.raises(CorruptionError, match="Bad magic number for file header"):
            reader.read(members[1])
        assert reader.read(members[2]) == b"sea " * 5
        assert [c.member_name for c in _wraps(reader)] == ["c.txt", ""]


def test_a_directory_out_of_file_order_below_4_gib_is_not_corrected() -> None:
    # An entry whose offset falls below the previous member's end in an archive with
    # nothing past 4 GiB: the corrected offset would be past the directory, so no
    # header is read there and the stored offset stays.
    data = _small_reversed()
    reads: list[int] = []

    def read_at(offset: int, n: int) -> bytes:
        reads.append(offset)
        return data[offset : offset + n]

    end = find_end_record(read_at, len(data))
    reads.clear()
    walk = CentralDirectoryWalk(
        read_at, end, local_name_at=lambda offset: local_name_at(read_at, offset)
    )
    entries = list(walk)
    assert [e.name for e in entries] == [b"b.txt", b"a.txt"]
    assert [e.offset_correction for e in entries] == [0, 0]
    assert all(offset < len(data) for offset in reads)
    assert walk.findings == []
    with open_archive(io.BytesIO(data), format=ArchiveFormat.ZIP) as reader:
        assert [reader.read(m) for m in reader.members()] == [b"bee", b"ay"]
        assert _wraps(reader) == []


def _small_reversed() -> bytes:
    layout = _layout([("a.txt", b"ay"), ("b.txt", b"bee")])
    stream = _Sparse(layout.segments, layout.size)
    data = stream.read(layout.size)
    cd = layout.cd_offset
    # Swap the two 51- and 51-byte central entries (same name length).
    size = (len(data) - 22 - cd) // 2
    first, second = data[cd : cd + size], data[cd + size : cd + 2 * size]
    return data[:cd] + second + first + data[cd + 2 * size :]


def test_a_pipe_corrects_the_same_offsets() -> None:
    # The pass streams past the 4 GiB member, so this test reads 4 GiB of zeros.
    layout = _layout(_PAST_4_GIB)
    source = _ForwardOnly(layout.segments, layout.size)
    with open_archive(source, format=ArchiveFormat.ZIP, streaming=True) as reader:
        names = [
            (member.name, None if stream is None else stream.read())
            for member, stream in reader.stream_members()
            if member.name != "big.bin" or stream is None
        ]
        wraps = _wraps(reader)
    assert ("b.txt", b"bee " * 8) in names
    assert ("c.txt", b"sea " * 5) in names
    assert [(c.record, c.member_name) for c in wraps] == [
        ("local_header", "b.txt"),
        ("local_header", "c.txt"),
        ("central_directory", ""),
    ]
