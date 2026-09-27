"""Seekable lzip decoder for :class:`~.decompressor_stream.DecompressorStream`.

Opened via :class:`~.codecs.LzipCodec` / ``open_codec_stream(Codec.LZIP, …)``.
Pure-stdlib over Python's ``lzma`` module.

lzip format (per the lzip manual): a file is a sequence of one or more *members* that may
be concatenated freely. Each member carries its own sizes in a 20-byte trailer (the
"distributed index"), enabling random access without a separate index.

  Per member:
    Header  (6 bytes):  magic b"LZIP"(4) + version(1, must be 1) +
                        coded_dict(1; dict_size = 1 << (coded_dict & 0x1F), exp 12-29)
    LZMA1 data:         raw LZMA1 with an end-of-stream marker, fixed lc=3,lp=0,pb=2.
                        The 13-byte LZMA_ALONE header is absent and synthesised here.
    Trailer (20 bytes): crc32(4 LE) + data_size(8 LE) + member_size(8 LE)

Spec: https://www.nongnu.org/lzip/manual/lzip_manual.html#File-format
"""

from __future__ import annotations

import lzma
import os
import struct
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import BinaryIO

from archivey.exceptions import CorruptionError, TruncatedError
from archivey.internal.config import DecoderLimits, check_decoder_memory
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.hashing import crc32_combine
from archivey.internal.streams.decompressor_stream import (
    TRAILING_DATA_CANDIDATES,
    TRAILING_DATA_SEARCH,
    BaseDecoder,
    DecodeOut,
    DecompressorStream,
    SeekPoint,
    SpacedCollector,
    build_index_backwards,
)

_MAGIC = b"LZIP"
_HEADER_SIZE = 6
_TRAILER_SIZE = 20

# lzip mandates lc=3, lp=0, pb=2: props byte = (pb*5 + lp)*9 + lc = 93 = 0x5D.
_PROPS_BYTE = bytes([0x5D])
# "uncompressed size unknown" sentinel — relies on the in-stream EOS marker.
_UNKNOWN_SIZE = b"\xff" * 8


@dataclass
class _MemberBounds:
    compressed_start: int
    decompressed_start: int
    compressed_size: int
    decompressed_size: int

    @property
    def decompressed_end(self) -> int:
        return self.decompressed_start + self.decompressed_size


def _member_ends_at(
    stream: BinaryIO, end: int, stop_at: int, window: bytes = b"", base: int = 0
) -> bool:
    """Whether a member's trailer ends at ``end``: its size leads back to a header.

    ``window`` holds the source's bytes from ``base``; bytes it covers are not read
    again.
    """
    if end - stop_at < _HEADER_SIZE + _TRAILER_SIZE:
        return False
    if end - _TRAILER_SIZE >= base and end <= base + len(window):
        trailer = window[end - _TRAILER_SIZE - base : end - base]
    else:
        stream.seek(end - _TRAILER_SIZE)
        trailer = stream.read(_TRAILER_SIZE)
    if len(trailer) < _TRAILER_SIZE:
        return False
    member_size = int.from_bytes(trailer[12:20], "little")
    start = end - member_size
    if member_size < _HEADER_SIZE + _TRAILER_SIZE or start < stop_at:
        return False
    if base <= start and start + 5 <= base + len(window):
        header = window[start - base : start - base + 5]
    else:
        stream.seek(start)
        header = stream.read(5)
    return header == _MAGIC + b"\x01"


def _data_end(stream: BinaryIO, file_size: int, stop_at: int) -> int:
    """Where the lzip data ends: ``file_size``, or the last member before trailing data.

    The lzip manual allows data after the last member (§7), and ``lzip`` finds the
    last member the same way: the latest trailer, within the final
    :data:`TRAILING_DATA_SEARCH` bytes, whose member size leads back to a member
    header. A member size is below the file size, so its high bytes are zero; only
    ends behind such a run are tried, at most :data:`TRAILING_DATA_CANDIDATES` of
    them, since inside a long run of zeros every offset is one. The forward decoder
    reports the appended bytes when a read reaches them.
    """
    if _member_ends_at(stream, file_size, stop_at):
        return file_size
    base = max(stop_at, file_size - TRAILING_DATA_SEARCH)
    stream.seek(base)
    window = stream.read(file_size - base)
    zeros = b"\x00" * max(1, 8 - (file_size.bit_length() + 7) // 8)
    # A trailer ends at most 8 bytes past its last non-zero byte (the high bytes of its
    # member size), so a run of zero padding at the end is skipped in one step.
    at = min(len(window), len(window.rstrip(b"\x00")) + 8)
    for _ in range(TRAILING_DATA_CANDIDATES):
        at = window.rfind(zeros, 0, at)
        if at < 0:
            break
        end = base + at + len(zeros)
        if _member_ends_at(stream, end, stop_at, window, base):
            return end
        at += len(zeros) - 1
    raise CorruptionError(
        "Lzip trailer not found at the end of the file or in the "
        f"{len(window)} bytes before it (at most {TRAILING_DATA_CANDIDATES} "
        "candidates tried)"
    )


def _iter_trailers_backwards(
    stream: BinaryIO, file_size: int, stop_at: int = 0
) -> Iterator[tuple[int, int, int, int]]:
    """Yield ``(compressed_start, data_size, member_size, crc32)`` from the last member back.

    Reads only trailers and the 4-byte magic at each computed member start — no
    decompression. The magic check catches a corrupt ``member_size`` before it cascades
    into wrong offsets for every earlier member. Data after the last member is looked
    past (:func:`_data_end`).
    """
    compressed_end = _data_end(stream, file_size, stop_at)
    while compressed_end > stop_at:
        if compressed_end < _TRAILER_SIZE:
            raise CorruptionError("Lzip file is too small to contain a valid trailer")
        stream.seek(compressed_end - _TRAILER_SIZE)
        trailer = stream.read(_TRAILER_SIZE)
        if len(trailer) < _TRAILER_SIZE:
            raise CorruptionError("Lzip file truncated during backward index scan")
        crc32, data_size, member_size = struct.unpack_from("<IQQ", trailer, 0)
        if member_size < _HEADER_SIZE + _TRAILER_SIZE:
            raise CorruptionError(
                f"Lzip member_size {member_size} in trailer is too small to be valid"
            )
        compressed_start = compressed_end - member_size
        if compressed_start < 0:
            raise CorruptionError(
                f"Lzip member_size {member_size} exceeds remaining file size"
            )
        stream.seek(compressed_start)
        magic = stream.read(4)
        if magic != _MAGIC:
            raise CorruptionError(
                f"Lzip magic not found at expected member start {compressed_start} "
                f"(got {magic!r}); member_size in trailer may be corrupt"
            )
        yield compressed_start, int(data_size), int(member_size), int(crc32)
        compressed_end = compressed_start


def _read_index_backwards(
    stream: BinaryIO,
    file_size: int,
    stop_at: int = 0,
    start_decompressed_offset: int = 0,
    on_thinned: Callable[[], None] | None = None,
) -> list[_MemberBounds]:
    """Build the member index by scanning trailers backwards (no decompression).

    A member can be as small as 26 bytes, so the members kept are thinned
    (:class:`SpacedCollector`) once there are more than the seek-table cap; the last
    member is always kept, so the total size stays exact. Callers that need only totals
    use :func:`peek_index_summary`, which folds the walk and keeps nothing.
    """
    # Each entry is (decompressed distance from member start to the end, trailer).
    # Walking backwards that distance only grows, which is what the collector needs.
    kept: SpacedCollector[tuple[int, tuple[int, int, int, int]]] = SpacedCollector(
        lambda e: e[0]
    )
    total = 0
    for entry in _iter_trailers_backwards(stream, file_size, stop_at):
        total += entry[1]
        kept.add((total, entry))
    if kept.thinned and on_thinned is not None:
        on_thinned()
    end = start_decompressed_offset + total
    return [
        _MemberBounds(comp_start, end - dist, comp_size, decomp_size)
        for dist, (comp_start, decomp_size, comp_size, _crc) in reversed(kept.items)
    ]


def peek_index_summary(stream: BinaryIO, file_size: int) -> tuple[int, int]:
    """One backward index scan → ``(total_decompressed_size, combined_crc32)``.

    Folds each trailer into a running suffix total as the walk goes, so the listing-time
    probe holds no per-member state however many members the file declares.
    """
    total = 0
    crc = 0
    for _start, data_size, _member_size, member_crc in _iter_trailers_backwards(
        stream, file_size
    ):
        # Walking backwards, the member goes in front of the suffix combined so far. An
        # empty member contributes nothing, whatever CRC its trailer claims.
        if data_size:
            crc = crc32_combine(member_crc, crc, total)
            total += data_size
    return total, crc


class _LzipState:
    """Streaming state machine for multi-member lzip decompression.

    Cycles per member: NEED_HEADER → IN_MEMBER → NEED_TRAILER → NEED_HEADER. ``feed`` and
    ``flush`` return ``(decompressed_bytes, completed_members)`` where each completed
    member is ``(decompressed_size, compressed_size)``.
    """

    _NEED_HEADER = 0
    _IN_MEMBER = 1
    _NEED_TRAILER = 2

    def __init__(self, limits: DecoderLimits) -> None:
        self._limits = limits
        self._state = self._NEED_HEADER
        self._buf = bytearray()
        self._dec: lzma.LZMADecompressor | None = None
        self._crc = 0
        self._member_size = 0
        # Compressed bytes of the current member consumed so far, header included; the
        # trailer's member_size is checked against it (plus the trailer) in
        # _verify_trailer, because LzipDecoder uses member_size as a seek offset.
        self._member_comp_size = 0
        self._finished = False
        self._members_seen = 0
        self.truncated = False
        # Bytes fed past the last member that start no further one (see Decoder).
        self.trailing_bytes: int | None = None

    def feed(
        self, data: bytes, max_length: int = -1
    ) -> tuple[bytes, list[tuple[int, int]]]:
        if self._finished:
            if self.trailing_bytes is None:
                self._end_at(data)  # only zeros so far: keep looking
            return b"", []
        self._buf.extend(data)
        return self._process(max_length=max_length)

    def flush(self) -> tuple[bytes, list[tuple[int, int]]]:
        if self._state == self._NEED_HEADER:
            head = bytes(self._buf[:4])
            if self._members_seen == 0:
                # The source ended before a first header. Nothing, or the start of the
                # magic, is a cut-short lzip file; anything else was never one, since
                # trailing data is allowed only *after* a member (lzip spec §7).
                if head != _MAGIC[: len(head)]:
                    raise CorruptionError(
                        f"Not a valid lzip file: expected magic {_MAGIC!r}, got {head!r}"
                    )
                self.truncated = True
                return b"", []
            if head == _MAGIC:
                self.truncated = True
                return b"", []
            self._end_at(bytes(self._buf))
            return b"", []
        out, units = self._process(max_length=-1)
        if self._finished:
            return out, units
        if self._dec is not None and not self._dec.needs_input:
            try:
                more = self._dec.decompress(b"")
                out = out + more
                self._crc = zlib.crc32(more, self._crc) & 0xFFFFFFFF
            except lzma.LZMAError:
                pass
        self.truncated = True
        return out, units

    def _end_at(self, rest: bytes) -> None:
        """End the data before ``rest``: bytes after a member that start no further one.

        The lzip manual allows them (§7) and ``lzip`` ignores them unless asked not
        to; archivey reports them. Zeros are padding and pass.
        """
        self._finished = True
        self._buf.clear()
        rest = rest.lstrip(b"\x00")
        if rest:
            self.trailing_bytes = len(rest)

    def is_finished(self) -> bool:
        return self._finished

    @property
    def needs_input(self) -> bool:
        if self._finished:
            return True
        if self._dec is not None and not self._dec.needs_input:
            return False
        return not self._buf

    def _process(self, max_length: int = -1) -> tuple[bytes, list[tuple[int, int]]]:
        output = bytearray()
        new_members: list[tuple[int, int]] = []
        while True:
            if max_length >= 0 and len(output) >= max_length:
                break
            if self._state == self._NEED_HEADER:
                if len(self._buf) < _HEADER_SIZE:
                    break
                header = bytes(self._buf[:_HEADER_SIZE])
                if not self._start_member(header):
                    self._end_at(bytes(self._buf))
                    break
                del self._buf[:_HEADER_SIZE]
                self._state = self._IN_MEMBER

            elif self._state == self._IN_MEMBER:
                assert self._dec is not None
                if not self._buf and self._dec.needs_input:
                    break
                chunk = bytes(self._buf)
                self._buf.clear()
                try:
                    remaining = max_length - len(output) if max_length >= 0 else -1
                    plain = self._dec.decompress(chunk, remaining)
                except lzma.LZMAError as e:
                    raise CorruptionError(f"Error reading Lzip archive: {e}") from e
                self._member_comp_size += len(chunk)
                if plain:
                    self._crc = zlib.crc32(plain, self._crc)
                    self._member_size += len(plain)
                    output.extend(plain)
                if self._dec.eof:
                    self._member_comp_size -= len(self._dec.unused_data)
                    self._buf[0:0] = self._dec.unused_data
                    self._dec = None
                    self._state = self._NEED_TRAILER
                elif not self._dec.needs_input:
                    if not plain and not chunk:
                        break
                    continue
                else:
                    break

            elif self._state == self._NEED_TRAILER:
                if len(self._buf) < _TRAILER_SIZE:
                    break
                trailer = bytes(self._buf[:_TRAILER_SIZE])
                del self._buf[:_TRAILER_SIZE]
                new_members.append(self._verify_trailer(trailer))
                self._state = self._NEED_HEADER
        return bytes(output), new_members

    def _start_member(self, header: bytes) -> bool:
        """Initialise a new member; return ``False`` on valid trailing data after a member."""
        if header[:4] != _MAGIC:
            if self._members_seen == 0:
                raise CorruptionError(
                    f"Not a valid lzip file: expected magic {_MAGIC!r}, got {header[:4]!r}"
                )
            return False  # lzip spec §7: trailing data after members is allowed
        if header[4] != 1:
            raise CorruptionError(f"Unsupported lzip version: {header[4]}")
        exp = header[5] & 0x1F
        if not (12 <= exp <= 29):
            raise CorruptionError(
                f"Invalid lzip dict_size exponent {exp}: valid range is 12-29"
            )
        dict_size = 1 << exp
        # The format's own ceiling is 512 MiB (exponent 29), under the default cap, so
        # this refuses only for a caller who set a smaller one.
        check_decoder_memory(
            dict_size, limits=self._limits, what="lzip dictionary size"
        )
        lzma_alone_header = _PROPS_BYTE + struct.pack("<I", dict_size) + _UNKNOWN_SIZE
        try:
            self._dec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
            self._dec.decompress(lzma_alone_header)
        except lzma.LZMAError as e:
            raise CorruptionError(f"Error reading lzip header: {e}") from e
        self._crc = 0
        self._member_size = 0
        self._member_comp_size = _HEADER_SIZE
        return True

    def _verify_trailer(self, trailer: bytes) -> tuple[int, int]:
        crc32_stored, data_size, member_size = struct.unpack_from("<IQQ", trailer, 0)
        if (self._crc & 0xFFFFFFFF) != crc32_stored:
            raise CorruptionError(
                f"Lzip CRC32 mismatch: stored {crc32_stored:#010x}, "
                f"computed {self._crc & 0xFFFFFFFF:#010x}"
            )
        if self._member_size != data_size:
            raise CorruptionError(
                f"Lzip size mismatch: stored {data_size}, actual {self._member_size}"
            )
        actual_member_size = self._member_comp_size + _TRAILER_SIZE
        if member_size != actual_member_size:
            raise CorruptionError(
                f"Lzip member size mismatch: stored {member_size}, "
                f"actual {actual_member_size}"
            )
        self._members_seen += 1
        return (int(data_size), int(member_size))


class LzipDecoder(BaseDecoder):
    """Lzip decoder: member-start seek points (before-placement) + trailer index."""

    def __init__(
        self,
        state: _LzipState,
        *,
        comp_cursor: int,
        decomp_cursor: int,
        collector: DiagnosticCollector | None,
        limits: DecoderLimits,
    ) -> None:
        self._state = state
        self._limits = limits
        self._comp_cursor = comp_cursor
        self._decomp_cursor = decomp_cursor
        self._collector = collector

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> LzipDecoder:
        del inner
        return LzipDecoder(
            _LzipState(self._limits),
            comp_cursor=point.compressed_offset,
            decomp_cursor=point.decompressed_offset,
            collector=self._collector,
            limits=self._limits,
        )

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        data, units = self._state.feed(chunk, max_length=max_length)
        return DecodeOut(data, self._points_for_units(units))

    def flush(self) -> DecodeOut:
        data, units = self._state.flush()
        if self._state.truncated:
            self._pending_error = TruncatedError("Lzip file is truncated")
        return DecodeOut(data, self._points_for_units(units))

    @property
    def finished(self) -> bool:
        return self._state.is_finished() and not self._state.truncated

    @property
    def trailing_bytes(self) -> int | None:
        return self._state.trailing_bytes

    @property
    def needs_input(self) -> bool:
        return self._state.needs_input

    def _points_for_units(self, units: list[tuple[int, int]]) -> list[SeekPoint]:
        points: list[SeekPoint] = []
        for decompressed_size, compressed_size in units:
            # Before-placement: point at member start, then advance cursors.
            points.append(SeekPoint(self._decomp_cursor, self._comp_cursor))
            self._comp_cursor += compressed_size
            self._decomp_cursor += decompressed_size
        return points

    def build_index(
        self, inner: BinaryIO, last_known: SeekPoint
    ) -> tuple[list[SeekPoint], int | None]:
        return build_index_backwards(
            inner,
            last_known,
            _read_index_backwards,
            lambda m: SeekPoint(m.decompressed_start, m.compressed_start),
            "Lzip backwards index scan failed (the file may have trailing data after the "
            "last member, which is valid per the lzip spec); falling back to sequential "
            "decompression. Reason: %s",
            codec_name="lzip",
            collector=self._collector,
            scan="backwards_trailer",
        )


def LzipDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
    *,
    collector: DiagnosticCollector | None = None,
    seekable: bool = True,
    decoder_limits: DecoderLimits = DecoderLimits(),
    report_trailing_data: bool = False,
) -> DecompressorStream:
    """Seekable lzip decompressor backed by stdlib ``lzma``.

    ``decoder_limits`` caps each member header's dictionary size; it defaults to the
    public default, not to no cap.
    """

    def make_decoder(point: SeekPoint, inner: BinaryIO) -> LzipDecoder:
        del inner
        return LzipDecoder(
            _LzipState(decoder_limits),
            comp_cursor=point.compressed_offset,
            decomp_cursor=point.decompressed_offset,
            collector=collector,
            limits=decoder_limits,
        )

    return DecompressorStream(
        path,
        make_decoder=make_decoder,
        collector=collector,
        codec_name="lzip",
        seekable=seekable,
        report_trailing_data=report_trailing_data,
    )
