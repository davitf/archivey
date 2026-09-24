"""Seekable XZ decoder for :class:`~.decompressor_stream.DecompressorStream`.

Opened via :class:`~.codecs.XzCodec` / ``open_codec_stream(Codec.XZ, …)``. Backward
index scan + streaming state machine over stdlib ``lzma``.

XZ binary format (summary):
  A file is a sequence of one or more XZ streams, optionally separated by 4-byte-aligned
  null padding.

  Per stream:
    Header  (12 bytes): magic(6) + stream flags(2) + CRC32(4)
    Blocks  (variable): one or more LZMA2 blocks
    Index   (variable): MBI-encoded list of (unpadded_size, uncompressed_size) per block;
                        preceded by a 0x00 indicator byte; padded to a 4-byte boundary;
                        followed by CRC32(4)
    Footer  (12 bytes): CRC32(4) + backward_size(4) + stream flags(2) + magic(2)

  backward_size encodes the index size: actual_bytes = (value + 1) * 4.

XZ spec: https://tukaani.org/xz/xz-file-format.txt
"""

from __future__ import annotations

import lzma
import os
import struct
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO, Callable

from archivey.diagnostics import DiagnosticCode, SeekIndexContext
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    ResourceLimitError,
    TruncatedError,
)
from archivey.internal.config import DecoderLimits
from archivey.internal.diagnostics_collector import (
    DiagnosticCollector,
    resolve_collector,
)
from archivey.internal.logs import streams as logger
from archivey.internal.streams.decompressor_stream import (
    SEEK_TABLE_THINNED,
    BaseDecoder,
    DecodeOut,
    DecompressorStream,
    SeekPoint,
    SpacedCollector,
    build_index_backwards,
)

_XZ_STREAM_MAGIC = b"\xfd7zXZ\x00"
_XZ_FOOTER_MAGIC = b"YZ"
_STREAM_HEADER_SIZE = 12
_STREAM_FOOTER_SIZE = 12


def _round_up_4(n: int) -> int:
    return (n + 3) & ~3


def _decode_mbi(data: bytes, offset: int) -> tuple[int, int]:
    """Decode a multi-byte integer at ``offset``; return ``(value, bytes_consumed)``."""
    value = 0
    shift = 0
    for i in range(9):  # XZ spec: max 9 bytes per MBI
        if offset + i >= len(data):
            raise CorruptionError("XZ index MBI truncated")
        byte = data[offset + i]
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            if byte == 0 and i > 0:
                # XZ spec §1.2 requires the shortest encoding; liblzma rejects a
                # trailing 0x00 byte, so the seek index does too.
                raise CorruptionError("XZ index MBI is not minimally encoded")
            return value, i + 1
        shift += 7
    raise CorruptionError("XZ index MBI exceeds 9 bytes")


def _encode_mbi(value: int) -> bytes:
    if value == 0:
        return b"\x00"
    result = bytearray()
    while value > 0:
        byte = value & 0x7F
        value >>= 7
        if value > 0:
            byte |= 0x80
        result.append(byte)
    return bytes(result)


@dataclass
class _XzBlockBounds:
    """One block's place in the file, and what resuming from it needs.

    The ``stream_*`` fields come from the stream's footer and index, not from the other
    blocks: ``_XzBlockResume`` decodes from this block to where the stream's blocks end,
    so a seek table can keep any subset of a stream's blocks.
    """

    compressed_start: int
    decompressed_start: int
    unpadded_size: int
    uncompressed_size: int
    check: int
    # Where the stream's last block ends and its index starts.
    blocks_end: int
    stream_decompressed_end: int
    # Just past the stream footer, where the next stream (or padding) begins.
    stream_compressed_end: int

    @property
    def decompressed_end(self) -> int:
        return self.decompressed_start + self.uncompressed_size


def _parse_xz_header(data: bytes) -> int:
    if len(data) < _STREAM_HEADER_SIZE:
        raise CorruptionError(f"XZ stream header too short: {len(data)} bytes")
    if data[:6] != _XZ_STREAM_MAGIC:
        raise CorruptionError(f"XZ stream header magic not found (got {data[:6]!r})")
    stream_flags = data[6:8]
    stored_crc = struct.unpack_from("<I", data, 8)[0]
    computed_crc = zlib.crc32(stream_flags) & 0xFFFFFFFF
    if stored_crc != computed_crc:
        raise CorruptionError(
            f"XZ stream header CRC32 mismatch: stored {stored_crc:#010x}, "
            f"computed {computed_crc:#010x}"
        )
    if stream_flags[0] != 0:
        raise CorruptionError(
            f"XZ stream flags reserved byte is non-zero: {stream_flags[0]:#04x}"
        )
    return stream_flags[1] & 0x0F


def _parse_xz_footer(data: bytes) -> tuple[int, int]:
    if len(data) < _STREAM_FOOTER_SIZE:
        raise CorruptionError(f"XZ stream footer too short: {len(data)} bytes")
    if data[10:12] != _XZ_FOOTER_MAGIC:
        raise CorruptionError(
            f"XZ stream footer magic 'YZ' not found (got {data[10:12]!r})"
        )
    stored_crc = struct.unpack_from("<I", data, 0)[0]
    backward_size_raw = struct.unpack_from("<I", data, 4)[0]
    stream_flags = data[8:10]
    computed_crc = zlib.crc32(data[4:10]) & 0xFFFFFFFF
    if stored_crc != computed_crc:
        raise CorruptionError(
            f"XZ stream footer CRC32 mismatch: stored {stored_crc:#010x}, "
            f"computed {computed_crc:#010x}"
        )
    if stream_flags[0] != 0:
        raise CorruptionError(
            f"XZ stream flags reserved byte is non-zero: {stream_flags[0]:#04x}"
        )
    check = stream_flags[1] & 0x0F
    backward_size_bytes = (backward_size_raw + 1) * 4
    return check, backward_size_bytes


def _parse_xz_index(data: bytes) -> list[tuple[int, int]]:
    """Parse one stream's index into ``(unpadded_size, uncompressed_size)`` records."""
    return list(_iter_xz_index(data))


def _iter_xz_index(data: bytes) -> Iterator[tuple[int, int]]:
    """Yield one stream's ``(unpadded_size, uncompressed_size)`` records, validating.

    A record can be as small as two bytes, so callers that may meet millions walk the
    records rather than store them. The length and padding checks run after the last
    record, so a caller must exhaust the iterator for the index to count as valid.
    """
    if not data or data[0] != 0x00:
        raise CorruptionError(
            f"XZ index indicator byte expected 0x00, got {data[0]:#04x}"
            if data
            else "XZ index is empty"
        )
    offset = 1
    num_records, consumed = _decode_mbi(data, offset)
    offset += consumed
    for _ in range(num_records):
        unpadded_size, consumed = _decode_mbi(data, offset)
        offset += consumed
        uncompressed_size, consumed = _decode_mbi(data, offset)
        offset += consumed
        if unpadded_size == 0:
            raise CorruptionError("XZ index: unpadded_size must be > 0")
        yield unpadded_size, uncompressed_size
    # The footer's backward size fixes the index length, so the records plus padding
    # must fill it exactly: fewer records than the index carries, or padding cut short,
    # is a malformed index (liblzma, which does the decoding, rejects both).
    padded_len = _round_up_4(offset)
    if padded_len != len(data):
        raise CorruptionError(
            f"XZ index length mismatch: records end at {padded_len}, "
            f"index is {len(data)} bytes"
        )
    for i in range(offset, padded_len):
        if data[i] != 0:
            raise CorruptionError(
                f"XZ index padding byte {i} is non-zero: {data[i]:#04x}"
            )


# Stream padding is scanned backwards this many bytes per read; a multiple of 4.
_PADDING_SCAN_CHUNK = 64 * 1024


def _skip_stream_padding_backwards(
    stream: BinaryIO, compressed_end: int, stop_at: int
) -> int:
    """Return the end of the stream before any zero padding that ends at ``compressed_end``.

    Stream padding (XZ spec §2.2) is 4-byte groups of zeros, aligned to the end being
    walked back from, and unbounded on a well-formed file. Most streams carry none, so
    the first read is one group; each all-zero read grows the next sixteenfold, up to
    :data:`_PADDING_SCAN_CHUNK`. No padding costs 4 bytes, and megabytes of it cost a
    few dozen reads rather than one ``seek`` + ``read(4)`` per group. The result is the
    offset just past the last group holding a non-zero byte, or a value ``<= stop_at``
    when every group down to ``stop_at`` is zero. As with a group-at-a-time walk, the
    lowest group may reach up to 3 bytes below ``stop_at``, and a group that would start
    before offset 0 is an error.
    """
    chunk_limit = 4
    while compressed_end > stop_at:
        groups = -(-(compressed_end - stop_at) // 4)
        groups = min(groups, chunk_limit // 4, compressed_end // 4)
        if groups == 0:
            raise CorruptionError("XZ file too small to contain a valid stream")
        start = compressed_end - 4 * groups
        stream.seek(start)
        chunk = stream.read(4 * groups)
        if len(chunk) < 4 * groups:
            raise CorruptionError("XZ file truncated during backward scan")
        nonzero_end = len(chunk.rstrip(b"\x00"))
        if nonzero_end:
            return start + _round_up_4(nonzero_end)
        compressed_end = start
        chunk_limit = min(chunk_limit * 16, _PADDING_SCAN_CHUNK)
    return compressed_end


def _read_xz_index_backwards(
    stream: BinaryIO,
    file_size: int,
    stop_at: int = 0,
    start_decompressed_offset: int = 0,
    on_thinned: Callable[[], None] | None = None,
) -> list[_XzBlockBounds]:
    """Walk XZ streams from EOF toward ``stop_at``, building a block index.

    Reads only footers and indices — no decompression. Returns blocks in forward order
    with absolute compressed/decompressed offsets.

    Past the seek-table cap the blocks kept are thinned by decompressed distance
    (:class:`SpacedCollector`) and ``on_thinned`` is called; any subset of blocks
    resumes correctly (``_XzBlockResume``). Within a stream the records are walked
    forward and thinned there first, so a stream declaring millions of blocks is never
    held whole. The last block is always kept, so the total stays exact.
    """
    # Keyed on decompressed distance from the block's start to the end of the scan,
    # which only grows as the walk goes back.
    kept: SpacedCollector[tuple[int, _XzBlockBounds]] = SpacedCollector(lambda e: e[0])
    thinned = False
    total = 0
    compressed_end = file_size

    while compressed_end > stop_at:
        compressed_end = _skip_stream_padding_backwards(stream, compressed_end, stop_at)

        if compressed_end <= stop_at:
            break

        if compressed_end < _STREAM_FOOTER_SIZE:
            raise CorruptionError(
                f"Not enough bytes for XZ footer at offset {compressed_end}"
            )
        stream.seek(compressed_end - _STREAM_FOOTER_SIZE)
        footer_data = stream.read(_STREAM_FOOTER_SIZE)
        if len(footer_data) < _STREAM_FOOTER_SIZE:
            raise CorruptionError("XZ footer truncated")
        check, index_size_bytes = _parse_xz_footer(footer_data)

        index_end = compressed_end - _STREAM_FOOTER_SIZE
        index_with_crc_start = index_end - index_size_bytes
        if index_with_crc_start < 0:
            raise CorruptionError("XZ index extends before start of file")

        stream.seek(index_with_crc_start)
        index_with_crc = stream.read(index_size_bytes)
        if len(index_with_crc) < index_size_bytes:
            raise CorruptionError("XZ index truncated")

        raw_index = index_with_crc[:-4]
        stored_index_crc = struct.unpack_from(
            "<I", index_with_crc, len(index_with_crc) - 4
        )[0]
        computed_index_crc = zlib.crc32(raw_index) & 0xFFFFFFFF
        if stored_index_crc != computed_index_crc:
            raise CorruptionError(
                f"XZ index CRC32 mismatch: stored {stored_index_crc:#010x}, "
                f"computed {computed_index_crc:#010x}"
            )

        blocks_compressed_total = 0
        stream_size = 0
        for unpadded_size, uncompressed_size in _iter_xz_index(raw_index):
            blocks_compressed_total += _round_up_4(unpadded_size)
            stream_size += uncompressed_size
        stream_header_start = (
            index_with_crc_start - blocks_compressed_total - _STREAM_HEADER_SIZE
        )
        if stream_header_start < 0:
            raise CorruptionError("XZ stream header start computes to negative offset")

        stream.seek(stream_header_start)
        header_data = stream.read(_STREAM_HEADER_SIZE)
        if len(header_data) < _STREAM_HEADER_SIZE:
            raise CorruptionError("XZ stream header truncated")
        header_check = _parse_xz_header(header_data)
        if header_check != check:
            raise CorruptionError(
                f"XZ stream header check {header_check} != footer check {check}"
            )

        # Blocks in forward order, offsets relative to the stream for now. Thinned on
        # their own (keyed on the in-stream offset) and the stream's last block always
        # added back, so the collector below sees them last first.
        in_stream: SpacedCollector[_XzBlockBounds] = SpacedCollector(
            lambda b: b.decompressed_start
        )
        block_compressed_start = stream_header_start + _STREAM_HEADER_SIZE
        block_decompressed_start = 0
        last: _XzBlockBounds | None = None
        for unpadded_size, uncompressed_size in _iter_xz_index(raw_index):
            last = _XzBlockBounds(
                compressed_start=block_compressed_start,
                decompressed_start=block_decompressed_start,
                unpadded_size=unpadded_size,
                uncompressed_size=uncompressed_size,
                check=check,
                blocks_end=index_with_crc_start,
                stream_decompressed_end=stream_size,
                stream_compressed_end=compressed_end,
            )
            in_stream.add(last)
            block_compressed_start += _round_up_4(unpadded_size)
            block_decompressed_start += uncompressed_size
        blocks = in_stream.items
        if last is not None and blocks[-1] is not last:
            blocks.append(last)
        thinned = thinned or in_stream.thinned
        # Relative offsets become distances from the end of the scan.
        for block in reversed(blocks):
            kept.add((total + stream_size - block.decompressed_start, block))
        total += stream_size
        compressed_end = stream_header_start

    thinned = thinned or kept.thinned
    if thinned and on_thinned is not None:
        on_thinned()
    end = start_decompressed_offset + total
    result: list[_XzBlockBounds] = []
    for dist, block in reversed(kept.items):
        start = end - dist
        # stream_decompressed_end still holds the stream's size.
        block.stream_decompressed_end += start - block.decompressed_start
        block.decompressed_start = start
        result.append(block)
    return result


# liblzma's ``memlimit`` counts a decoder's whole working set: the dictionary the
# block header declares plus the decoder's own overhead, measured on liblzma 5.4.5 at
# 65 592 bytes for a lone LZMA2 filter and 67 992 for the longest chain xz allows
# (four filters). The allowance, about twice that, keeps a dictionary exactly at
# ``max_decoder_memory`` readable, as it is on the raw LZMA and .lzma paths, which
# compare the declared dictionary itself. It is kept small because it is also slack:
# a cap set 128 KiB or less below one of the sizes an xz header can declare (2^n or
# 3 * 2^(n-1)) admits that size on xz alone.
_LIBLZMA_OVERHEAD_ALLOWANCE = 128 * 1024
_LIBLZMA_MEMLIMIT_MAX = 2**64 - 1


def _new_decompressor(limits: DecoderLimits) -> lzma.LZMADecompressor:
    """An xz decompressor that refuses a block whose filters need more than the cap.

    xz is the one LZMA path archivey does not read the dictionary size off itself:
    each block header declares its own, and ``_XzState`` hands liblzma whole streams
    without walking their blocks. liblzma compares ``memlimit`` against the block's
    filter chain after decoding the block header and before allocating anything for
    it, which is the same guarantee :func:`~archivey.internal.config.check_decoder_memory`
    gives the other paths.
    """
    cap = limits.max_decoder_memory
    # liblzma's memlimit is a uint64. A cap whose allowance-padded value does not fit
    # refuses nothing liblzma could be asked for, so it is left off, as for no cap.
    if cap is None or cap + _LIBLZMA_OVERHEAD_ALLOWANCE > _LIBLZMA_MEMLIMIT_MAX:
        return lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
    return lzma.LZMADecompressor(
        format=lzma.FORMAT_XZ, memlimit=cap + _LIBLZMA_OVERHEAD_ALLOWANCE
    )


def _lzma_failure(
    exc: lzma.LZMAError, context: str, limits: DecoderLimits
) -> ArchiveyError:
    """Map a liblzma error to archivey's, telling a memlimit refusal from corruption.

    CPython reports ``LZMA_MEMLIMIT_ERROR`` only as message text, "Memory usage limit
    exceeded" on every version from 3.10 to 3.14, so the text is what there is to
    match on. Only a decompressor built with a ``memlimit`` can raise it.
    """
    cap = limits.max_decoder_memory
    if cap is not None and str(exc).startswith("Memory usage limit"):
        return ResourceLimitError(
            f"Decoder limit reached: max_decoder_memory={cap} (an xz block's "
            f"declared dictionary needs more than that; liblzma refused it before "
            f"allocating). The archive chose this number; raise "
            f"DecoderLimits.max_decoder_memory if the archive is trusted."
        )
    return CorruptionError(f"{context}: {exc}")


class _XzState:
    """Streaming state machine for multi-stream XZ decompression."""

    _NEED_HEADER = 0
    _IN_STREAM = 1

    def __init__(self, limits: DecoderLimits, *, after_stream: bool = False) -> None:
        """``after_stream``: start just past a stream decoded elsewhere, where padding,
        more streams or the end of the data may follow, as after any stream here."""
        self._limits = limits
        self._state = self._NEED_HEADER
        self._buf = bytearray()
        self._dec: lzma.LZMADecompressor | None = None
        self._bytes_fed = 0
        self._streams_seen = 1 if after_stream else 0
        self._finished = False
        self._stream_decomp_bytes = 0
        # Stream padding stripped since the last stream ended. It is counted into the
        # next stream's compressed size so that XzDecoder's compressed cursor stays on
        # real file offsets; the per-stream backward scan reads the footer from there.
        self._padding_before_stream = 0
        self.truncated = False

    def feed(
        self, data: bytes, max_length: int = -1
    ) -> tuple[bytes, list[tuple[int, int]]]:
        self._buf.extend(data)
        return self._process(max_length=max_length)

    def flush(self) -> tuple[bytes, list[tuple[int, int]]]:
        if self._state == self._NEED_HEADER:
            if self._streams_seen == 0:
                # The source ended before a first header: nothing, or the start of the
                # magic, is a cut-short xz file; anything else was never one.
                head = bytes(self._buf[:6])
                if self._padding_before_stream or head != _XZ_STREAM_MAGIC[: len(head)]:
                    raise CorruptionError("Not a valid XZ file: no streams found")
                self.truncated = True
                return b"", []
            if len(self._buf) >= 6 and bytes(self._buf[:6]) == _XZ_STREAM_MAGIC:
                self.truncated = True
                return b"", []
            self._finished = True
            return b"", []
        # Mid-stream: drain any remaining buffered input for a recoverable prefix,
        # then arm truncation (decoder owns pending_error).
        out, units = self._process(max_length=-1)
        if self._finished:
            return out, units
        if self._dec is not None and not self._dec.needs_input:
            try:
                more = self._dec.decompress(b"")
                out = out + more
            except lzma.LZMAError as e:
                # A corrupt tail is what truncation looks like here, so it stays a
                # truncation. A memlimit refusal is not damage and is not swallowed.
                failure = _lzma_failure(e, "XZ decompression error", self._limits)
                if isinstance(failure, ResourceLimitError):
                    raise failure from e
        self.truncated = True
        return out, units

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
        new_streams: list[tuple[int, int]] = []
        while True:
            if max_length >= 0 and len(output) >= max_length:
                break
            if self._state == self._NEED_HEADER:
                # XZ spec §2.2 "Stream Padding": concatenated streams may be separated by
                # null bytes whose length is a multiple of four (to keep streams 4-byte
                # aligned). Strip all leading 4-byte runs in one delete.
                padding = 0
                while (
                    padding + 4 <= len(self._buf)
                    and bytes(self._buf[padding : padding + 4]) == b"\x00\x00\x00\x00"
                ):
                    padding += 4
                if padding:
                    del self._buf[:padding]
                    self._padding_before_stream += padding

                if len(self._buf) < _STREAM_HEADER_SIZE:
                    break
                header = bytes(self._buf[:_STREAM_HEADER_SIZE])
                if header[:6] != _XZ_STREAM_MAGIC:
                    if self._streams_seen == 0:
                        raise CorruptionError(
                            f"Not a valid XZ file: expected magic {_XZ_STREAM_MAGIC!r}, "
                            f"got {header[:6]!r}"
                        )
                    self._finished = True
                    self._buf.clear()
                    break
                del self._buf[:_STREAM_HEADER_SIZE]
                try:
                    self._dec = _new_decompressor(self._limits)
                    remaining = max_length - len(output) if max_length >= 0 else -1
                    plain = self._dec.decompress(header, remaining)
                except lzma.LZMAError as e:
                    raise _lzma_failure(
                        e, "XZ stream header error", self._limits
                    ) from e
                self._bytes_fed = _STREAM_HEADER_SIZE
                self._stream_decomp_bytes = len(plain)
                output.extend(plain)
                self._state = self._IN_STREAM

            elif self._state == self._IN_STREAM:
                assert self._dec is not None
                if not self._buf and self._dec.needs_input:
                    break
                chunk = bytes(self._buf)
                self._buf.clear()
                try:
                    remaining = max_length - len(output) if max_length >= 0 else -1
                    plain = self._dec.decompress(chunk, remaining)
                except lzma.LZMAError as e:
                    raise _lzma_failure(
                        e, "XZ decompression error", self._limits
                    ) from e
                self._bytes_fed += len(chunk)
                self._stream_decomp_bytes += len(plain)
                output.extend(plain)
                if self._dec.eof:
                    unused = self._dec.unused_data
                    compressed_size = (
                        self._padding_before_stream + self._bytes_fed - len(unused)
                    )
                    self._padding_before_stream = 0
                    new_streams.append((self._stream_decomp_bytes, compressed_size))
                    self._streams_seen += 1
                    self._dec = None
                    self._buf[0:0] = unused
                    self._state = self._NEED_HEADER
                elif not self._dec.needs_input:
                    # More output available under the budget; loop to drain with b"".
                    # If a drain produced nothing, stop — avoid spinning on a stuck
                    # decompressor that reports needs_input=False with empty output.
                    if not plain and not chunk:
                        break
                    continue
                else:
                    break
        return bytes(output), new_streams


class _XzBlockResume:
    """Decode one XZ stream from one of its blocks to the end of its blocks.

    liblzma decodes whole streams, so it is given a synthetic stream header (built from
    the stream's check type) followed by the stream's real bytes from ``start`` up to
    where its index begins. Each block still has its header and check verified. The
    stream's index is not fed, since it lists the blocks before ``start`` too; instead
    the output must come to exactly the size the index gives for the rest of the
    stream. Needing only ``start`` is what lets a seek table keep any subset of blocks.

    Exposes the same feed/flush/is_finished interface as ``_XzState``. ``inner`` is
    shared with the ``DecompressorStream`` driving ``feed``, which reads ahead; this
    engine moves it only once, at construction, before anything is read.
    """

    def __init__(
        self, start: _XzBlockBounds, inner: BinaryIO, limits: DecoderLimits
    ) -> None:
        self._limits = limits
        self._to_feed = start.blocks_end - start.compressed_start
        self._to_output = start.stream_decompressed_end - start.decompressed_start
        self._finished = False
        self.truncated = False
        inner.seek(start.compressed_start)
        self._dec = _new_decompressor(limits)
        stream_flags = bytes([0x00, start.check])
        header_crc = zlib.crc32(stream_flags) & 0xFFFFFFFF
        synthetic_header = (
            _XZ_STREAM_MAGIC + stream_flags + struct.pack("<I", header_crc)
        )
        try:
            self._dec.decompress(synthetic_header)
        except lzma.LZMAError as e:
            raise CorruptionError(f"XZ synthetic header error: {e}") from e

    def feed(
        self, data: bytes, max_length: int = -1
    ) -> tuple[bytes, list[tuple[int, int]]]:
        if self._finished:
            return b"", []
        # Bytes past the blocks (the index, the footer, later streams) are dropped;
        # XzDecoder repositions inner once this engine has finished.
        chunk = data[: self._to_feed]
        self._to_feed -= len(chunk)
        try:
            plain = self._dec.decompress(chunk, max_length)
        except lzma.LZMAError as e:
            raise _lzma_failure(e, "XZ block decompression error", self._limits) from e
        self._to_output -= len(plain)
        if self._to_output < 0:
            raise CorruptionError(
                "XZ blocks decode to more than the stream index declares"
            )
        if self._to_feed == 0 and self._dec.needs_input:
            if self._to_output:
                raise CorruptionError(
                    "XZ blocks end where the stream index starts, "
                    f"{self._to_output} bytes short of the size it declares"
                )
            self._finished = True
        return plain, []

    def flush(self) -> tuple[bytes, list[tuple[int, int]]]:
        out, units = self.feed(b"")
        if not self._finished:
            self.truncated = True
        return out, units

    def is_finished(self) -> bool:
        return self._finished

    @property
    def needs_input(self) -> bool:
        return self._finished or self._dec.needs_input


class XzDecoder(BaseDecoder):
    """XZ decoder: sequential ``_XzState``, or a block resume via ``recreate``.

    A point carrying an ``_XzBlockBounds`` resumes with ``_XzBlockResume``, which ends
    with that block's stream. The decoder then moves ``inner`` just past the stream
    (``handoff``) and carries on with a sequential ``_XzState``. Everything a resume
    needs is on the point itself, so it never depends on which other points the seek
    table holds.
    """

    def __init__(
        self,
        engine: _XzState | _XzBlockResume,
        *,
        inner: BinaryIO,
        comp_cursor: int,
        decomp_cursor: int,
        index_enabled: bool,
        collector: DiagnosticCollector | None,
        get_seek_points: Callable[[], list[SeekPoint]],
        index_built: Callable[[], bool],
        limits: DecoderLimits,
        handoff: SeekPoint | None = None,
    ) -> None:
        self._engine = engine
        self._limits = limits
        self._handoff = handoff
        self._inner = inner
        self._comp_cursor = comp_cursor
        self._decomp_cursor = decomp_cursor
        self._index_enabled = index_enabled
        self._collector = collector
        self._get_seek_points = get_seek_points
        self._index_built = index_built

    @classmethod
    def from_point(
        cls,
        point: SeekPoint,
        inner: BinaryIO,
        *,
        index_enabled: bool,
        collector: DiagnosticCollector | None,
        get_seek_points: Callable[[], list[SeekPoint]],
        index_built: Callable[[], bool],
        limits: DecoderLimits,
    ) -> XzDecoder:
        handoff: SeekPoint | None = None
        if point.state is None:
            engine: _XzState | _XzBlockResume = _XzState(limits)
        else:
            start: _XzBlockBounds = point.state
            engine = _XzBlockResume(start, inner, limits)
            handoff = SeekPoint(
                start.stream_decompressed_end, start.stream_compressed_end
            )
        return cls(
            engine,
            inner=inner,
            comp_cursor=point.compressed_offset,
            decomp_cursor=point.decompressed_offset,
            index_enabled=index_enabled,
            collector=collector,
            get_seek_points=get_seek_points,
            index_built=index_built,
            limits=limits,
            handoff=handoff,
        )

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> XzDecoder:
        return XzDecoder.from_point(
            point,
            inner,
            index_enabled=self._index_enabled,
            collector=self._collector,
            get_seek_points=self._get_seek_points,
            index_built=self._index_built,
            limits=self._limits,
        )

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        data, units = self._engine.feed(chunk, max_length=max_length)
        points = self._points_for_units(units)
        self._hand_off_if_resume_done()
        return DecodeOut(data, points)

    def flush(self) -> DecodeOut:
        data, units = self._engine.flush()
        if getattr(self._engine, "truncated", False):
            self._pending_error = TruncatedError("XZ file is truncated")
        elif self._handoff is not None and self._engine.is_finished():
            # A guard, not a path DecompressorStream reaches today: feed() hands off as
            # soon as the resume finishes, and flush() runs only once inner is
            # exhausted, which cannot happen before the stream's index that the resume
            # point was read from. Should a resume ever finish here with the hand-off
            # still owed (a seek table that outlived the file it was built from), fail
            # loudly rather than report finished and publish a size that stops short.
            self._pending_error = TruncatedError("XZ file is truncated")
        return DecodeOut(data, self._points_for_units(units))

    def _hand_off_if_resume_done(self) -> None:
        """Continue sequentially past the stream once ``_XzBlockResume`` has finished.

        A finished resume has already dropped the rest of the chunk it was fed, so
        moving ``inner`` here cannot hand ``feed`` bytes it is still consuming.
        """
        if self._handoff is None or not self._engine.is_finished():
            return
        point = self._handoff
        self._handoff = None
        self._inner.seek(point.compressed_offset)
        self._engine = _XzState(self._limits, after_stream=True)
        self._comp_cursor = point.compressed_offset
        self._decomp_cursor = point.decompressed_offset

    @property
    def finished(self) -> bool:
        return self._engine.is_finished() and not getattr(
            self._engine, "truncated", False
        )

    @property
    def needs_input(self) -> bool:
        return self._engine.needs_input

    def _points_for_units(self, units: list[tuple[int, int]]) -> list[SeekPoint]:
        if not units:
            return []
        if isinstance(self._engine, _XzState):
            return self._progressive_stream_points(units)
        for decomp_size, comp_size in units:
            self._comp_cursor += comp_size
            self._decomp_cursor += decomp_size
        return []

    def _progressive_stream_points(
        self, new_streams: list[tuple[int, int]]
    ) -> list[SeekPoint]:
        points: list[SeekPoint] = []
        for decompressed_size, compressed_size in new_streams:
            stream_comp_start = self._comp_cursor
            stream_decomp_start = self._decomp_cursor
            stream_comp_end = stream_comp_start + compressed_size
            emitted_stream_start = False
            if (
                self._index_enabled
                and not self._index_built()
                and self._inner.seekable()
            ):
                saved_pos = self._inner.tell()
                try:
                    thinned: list[bool] = []
                    blocks = _read_xz_index_backwards(
                        self._inner,
                        stream_comp_end,
                        stop_at=stream_comp_start,
                        start_decompressed_offset=stream_decomp_start,
                        on_thinned=lambda: thinned.append(True),
                    )
                    if thinned:
                        resolve_collector(self._collector).emit(
                            code=DiagnosticCode.SEEK_INDEX_DEGRADED,
                            message=(
                                "XZ stream has more blocks than the seek-table cap; "
                                "kept a spaced subset, so seeks may decode further"
                            ),
                            context=SeekIndexContext(
                                codec="xz",
                                scan="per_stream",
                                error_type=SEEK_TABLE_THINNED,
                            ),
                            logger=logger,
                        )
                    # Prefer block-bounds (with resume state) for the stream start
                    # over a state=None placeholder — avoids colliding with a prior
                    # build_index point at the same decompressed offset. A thinned
                    # stream still keeps its first block.
                    for b in blocks:
                        if b.uncompressed_size == 0:
                            continue  # zero-length span is never a useful seek target
                        if b.decompressed_start < stream_decomp_start:
                            continue
                        if b.decompressed_start == stream_decomp_start:
                            if stream_decomp_start > 0:
                                points.append(
                                    SeekPoint(
                                        b.decompressed_start,
                                        b.compressed_start,
                                        state=b,
                                    )
                                )
                                emitted_stream_start = True
                            continue
                        points.append(
                            SeekPoint(b.decompressed_start, b.compressed_start, state=b)
                        )
                except CorruptionError as e:
                    message = (
                        "XZ per-stream backward scan failed; block-level seek points for "
                        f"this stream will not be available: {e}"
                    )
                    resolve_collector(self._collector).emit(
                        code=DiagnosticCode.SEEK_INDEX_DEGRADED,
                        message=message,
                        context=SeekIndexContext(
                            codec="xz",
                            scan="per_stream",
                            error_type=type(e).__name__,
                        ),
                        logger=logger,
                    )
                finally:
                    self._inner.seek(saved_pos)
            # Only emit a state=None stream-start placeholder when we have no block
            # info yet *and* the index is not already populated (a prior SEEK_END /
            # try_get_size build_index would already hold the richer block point).
            if (
                stream_decomp_start > 0
                and not emitted_stream_start
                and not self._index_built()
            ):
                points.append(
                    SeekPoint(stream_decomp_start, stream_comp_start, state=None)
                )
            self._comp_cursor = stream_comp_end
            self._decomp_cursor += decompressed_size
        return points

    def build_index(
        self, inner: BinaryIO, last_known: SeekPoint
    ) -> tuple[list[SeekPoint], int | None]:
        return build_index_backwards(
            inner,
            last_known,
            _read_xz_index_backwards,
            lambda b: SeekPoint(b.decompressed_start, b.compressed_start, state=b),
            "XZ backwards index scan failed; falling back to sequential decompression. "
            "Reason: %s",
            codec_name="xz",
            collector=self._collector,
            # Zero-uncompressed_size blocks share a decompressed_start with the next
            # real block; they are never useful seek targets and must not collide.
            include_block=lambda b: b.uncompressed_size > 0,
        )


def XzDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
    *,
    collector: DiagnosticCollector | None = None,
    seekable: bool = True,
    decoder_limits: DecoderLimits = DecoderLimits(),
) -> DecompressorStream:
    """Seekable XZ decompressor backed by stdlib ``lzma``.

    ``decoder_limits`` caps the dictionary each block header declares (see
    :func:`_new_decompressor`); it defaults to the public default, not to no cap.

    ``stream_cell`` late-binds the constructed stream so ``XzDecoder.recreate`` can
    read subsequent block ``SeekPoint``s / ``_index_built`` — the same coupling the
    old ``XzDecompressorStream._make_decompressor`` had via ``self``. Fine for XZ;
    when BGZF (or another indexed codec) needs the same, prefer an explicit
    seek-table / index-state handle passed into ``make_decoder`` rather than another
    private-attr cell.
    """
    stream_cell: list[DecompressorStream | None] = [None]

    def make_decoder(point: SeekPoint, inner: BinaryIO) -> XzDecoder:
        def get_seek_points() -> list[SeekPoint]:
            stream = stream_cell[0]
            assert stream is not None
            return stream._seek_points

        def index_built() -> bool:
            stream = stream_cell[0]
            assert stream is not None
            return stream._index_built

        return XzDecoder.from_point(
            point,
            inner,
            index_enabled=seekable,
            collector=collector,
            get_seek_points=get_seek_points,
            index_built=index_built,
            limits=decoder_limits,
        )

    stream = DecompressorStream(
        path,
        make_decoder=make_decoder,
        collector=collector,
        codec_name="xz",
        seekable=seekable,
    )
    stream_cell[0] = stream
    return stream
