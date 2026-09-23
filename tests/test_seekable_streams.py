"""Tests for the ``seekable-decompressor-streams`` capability: XZ block-index and lzip
trailer-scan random access, plus accelerator present/absent behaviour."""

from __future__ import annotations

import bz2
import gzip
import importlib.util
import io
import lzma
import random
import struct
import zlib
from collections.abc import Callable
from typing import Any, BinaryIO

import pytest

from archivey.config import REWIND_REDECODE_WARN_BYTES
from archivey.exceptions import (
    CorruptionError,
    PackageNotInstalledError,
    TruncatedError,
)
from archivey.internal.config import AcceleratorMode, StreamConfig
from archivey.internal.streams.codecs import Codec, open_codec_stream
from archivey.internal.streams.lzip import LzipDecompressorStream, _read_index_backwards
from archivey.internal.streams.unix_compress import UnixCompressDecompressorStream
from archivey.internal.streams.xz import XzDecompressorStream, _read_xz_index_backwards
from tests.conftest import requires, requires_zstd, zstd_backend
from tests.streams_util import (
    CountingBytesIO,
    make_lzip_member,
    make_multi_member_lzip,
    make_multi_stream_xz,
    make_multiblock_xz,
    make_unix_compress,
    xz_cli_available,
)

CONTENT = bytes(range(256)) * 200  # 51200 bytes, compressible but non-trivial

# The rewind diagnostic is cost-based: it reports the decoded progress a backward seek
# discards, against REWIND_REDECODE_WARN_BYTES. A rewind smaller than that is cheap and
# deliberately silent, so the rewind tests need a payload big enough to cross it.
REWIND_CONTENT = bytes(range(256)) * (2 * REWIND_REDECODE_WARN_BYTES // 256)
# Read past the threshold before rewinding, so the discarded progress qualifies.
REWIND_READ = REWIND_REDECODE_WARN_BYTES + 4096


# --- XZ seeking via the block index ----------------------------------------------------


def test_xz_forward_read_roundtrip() -> None:
    compressed = lzma.compress(CONTENT, format=lzma.FORMAT_XZ)
    with XzDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.read() == CONTENT


def test_xz_stream_padding_between_streams() -> None:
    """4-byte-aligned null padding between concatenated XZ streams is skipped."""
    part1 = b"first-part"
    part2 = b"second-part"
    stream1 = lzma.compress(part1, format=lzma.FORMAT_XZ)
    stream2 = lzma.compress(part2, format=lzma.FORMAT_XZ)
    data = stream1 + b"\x00" * 8 + stream2
    with XzDecompressorStream(io.BytesIO(data)) as stream:
        assert stream.read() == part1 + part2


def test_xz_seek_set_and_read() -> None:
    compressed = lzma.compress(CONTENT, format=lzma.FORMAT_XZ)
    with XzDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.seek(10000) == 10000
        assert stream.read(100) == CONTENT[10000:10100]


def test_xz_seek_end_reports_size() -> None:
    compressed = lzma.compress(CONTENT, format=lzma.FORMAT_XZ)
    with XzDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.seek(0, io.SEEK_END) == len(CONTENT)
        assert stream.read() == b""


def test_xz_try_get_size_uses_index_not_full_decode() -> None:
    compressed = make_multi_stream_xz([CONTENT, CONTENT])
    stream = XzDecompressorStream(io.BytesIO(compressed))
    assert stream.try_get_size() == 2 * len(CONTENT)
    stream.close()


def test_xz_size_then_read_multistream_no_collision() -> None:
    """SEEK_END / try_get_size before a forward read must not assert (F1a).

    build_index emits block-bounds points; a later forward pass must not collide a
    state=None stream-start placeholder onto the same decompressed offset.
    """
    parts = [b"A" * 5000, b"B" * 5000]
    compressed = make_multi_stream_xz(parts)
    plaintext = b"".join(parts)
    with XzDecompressorStream(io.BytesIO(compressed), seekable=True) as stream:
        assert stream.seek(0, io.SEEK_END) == len(plaintext)
        stream.seek(0)
        assert stream.read() == plaintext
    with XzDecompressorStream(io.BytesIO(compressed), seekable=True) as stream:
        assert stream.try_get_size() == len(plaintext)
        stream.seek(0)
        assert stream.read() == plaintext


def test_xz_zero_uncompressed_size_blocks_do_not_crash_index() -> None:
    """Crafted index with zero-size blocks must not raise AssertionError (F1b)."""
    from archivey.exceptions import ArchiveyError
    from archivey.internal.streams.xz import (
        _XZ_FOOTER_MAGIC,
        _XZ_STREAM_MAGIC,
        _encode_mbi,
        _round_up_4,
    )

    check = 0x00
    flags = bytes([0x00, check])
    header = (
        _XZ_STREAM_MAGIC + flags + struct.pack("<I", zlib.crc32(flags) & 0xFFFFFFFF)
    )
    records = [(10, 100), (10, 0), (10, 0)]
    body = b"\x00" + _encode_mbi(len(records))
    for unpadded, uncomp in records:
        body += _encode_mbi(unpadded) + _encode_mbi(uncomp)
    body += b"\x00" * (_round_up_4(len(body)) - len(body))
    index = body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)
    backward_raw = (len(index) // 4) - 1
    fbody = struct.pack("<I", backward_raw) + bytes([0x00, check])
    footer = (
        struct.pack("<I", zlib.crc32(fbody) & 0xFFFFFFFF) + fbody + _XZ_FOOTER_MAGIC
    )
    block_payload = b"\x00" * sum(_round_up_4(u) for u, _ in records)
    crafted = header + block_payload + index + footer

    with XzDecompressorStream(io.BytesIO(crafted), seekable=True) as stream:
        # Size discovery / index build must stay inside ArchiveyError (or succeed).
        try:
            size = stream.seek(0, io.SEEK_END)
            assert size == 100
        except ArchiveyError:
            pass
        except AssertionError:
            pytest.fail("zero-size XZ blocks must not raise AssertionError")


def test_xz_backward_seek_uses_block_index() -> None:
    """A backward seek decompresses only from a nearby block, not the whole stream."""
    compressed = make_multi_stream_xz([CONTENT, CONTENT, CONTENT])
    counting = CountingBytesIO(compressed)
    with XzDecompressorStream(counting) as stream:
        assert stream.read() == CONTENT * 3  # forward pass populates the index
        baseline = counting.bytes_read
        stream.seek(len(CONTENT) * 2 + 5)  # into the third stream
        assert (
            stream.read(50)
            == (CONTENT * 3)[len(CONTENT) * 2 + 5 : len(CONTENT) * 2 + 55]
        )
        # Re-reading from a block start must not re-read the entire compressed file.
        assert counting.bytes_read - baseline < len(compressed)


def test_xz_index_backwards_parses_blocks() -> None:
    compressed = make_multi_stream_xz([CONTENT, CONTENT])
    blocks = _read_xz_index_backwards(io.BytesIO(compressed), len(compressed))
    assert sum(b.uncompressed_size for b in blocks) == 2 * len(CONTENT)
    assert blocks[0].decompressed_start == 0


@pytest.mark.skipif(
    not xz_cli_available(),
    reason="the xz CLI is needed to build a multi-block (single-stream) XZ fixture",
)
def test_xz_multiblock_backward_seek_crosses_block_boundary() -> None:
    """A backward seek within a *single* multi-block XZ stream uses the block chain.

    ``lzma.compress`` emits one block per stream, so the in-stream "advance to the next
    block" path of ``_XzBlockChain`` is otherwise unexercised. Build a genuinely
    multi-block stream and seek so the read spans a block boundary.
    """
    compressed = make_multiblock_xz(CONTENT, block_size=8192)
    blocks = _read_xz_index_backwards(io.BytesIO(compressed), len(compressed))
    assert len(blocks) > 1  # genuinely multi-block within one stream

    with XzDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.read() == CONTENT  # forward pass populates the block index
        start = len(CONTENT) // 3
        length = len(CONTENT) // 2  # long enough to cross at least one block boundary
        stream.seek(start)
        assert stream.read(length) == CONTENT[start : start + length]


@pytest.mark.skipif(
    not xz_cli_available(),
    reason="the xz CLI is needed to build a multi-block (single-stream) XZ fixture",
)
@pytest.mark.parametrize(
    ("start", "length"),
    [
        (100_000, 150_000),  # resumes in block 1, crosses into blocks 2-3
        (65_536, -1),  # resumes exactly on a block start, reads to EOF
        (131_000, 70_000),  # crosses a boundary late in a feed chunk
    ],
)
def test_xz_multiblock_seek_serves_the_right_bytes_across_feed_chunks(
    start: int, length: int
) -> None:
    """A block-chain resume must not rewind ``inner`` under ``DecompressorStream``.

    Incompressible content makes the compressed stream larger than one
    ``DecompressorStream`` read, so ``_XzBlockChain`` advances to a contiguous block
    while bytes past that block's start are still in the chunk it is consuming. If the
    advance rewound ``inner``, the next read would hand those bytes over a second time
    and the output would carry a copy of the next block's header.
    """
    content = random.Random(4).randbytes(300_000)
    compressed = make_multiblock_xz(content, block_size=65536)
    blocks = _read_xz_index_backwards(io.BytesIO(compressed), len(compressed))
    assert len(blocks) > 3
    assert len(compressed) > 2 * 65536  # a resume spans several feed chunks

    with XzDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.read() == content  # forward pass populates the block index
        stream.seek(start)
        end = len(content) if length < 0 else start + length
        assert stream.read(length) == content[start:end]


@pytest.mark.skipif(
    not xz_cli_available(),
    reason="the xz CLI is needed to build a multi-block (single-stream) XZ fixture",
)
def test_xz_multistream_block_chain_resume_crosses_a_stream_gap() -> None:
    """The discontinuous hop (stream footer and padding between two blocks) still works.

    The chain may reposition ``inner`` only where it also drops the rest of the chunk
    it holds; this pins that path while the contiguous one no longer seeks.
    """
    rng = random.Random(5)
    part1 = rng.randbytes(200_000)
    part2 = rng.randbytes(200_000)
    compressed = (
        make_multiblock_xz(part1, block_size=65536)
        + b"\x00" * 8  # stream padding
        + make_multiblock_xz(part2, block_size=65536)
    )
    content = part1 + part2

    with XzDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.read() == content
        # The padding is counted into the compressed cursor, so the second stream's
        # backward scan finds its footer and every point past the origin has blocks.
        assert all(sp.state is not None for sp in stream._seek_points[1:])
        for start in (70_000, 150_000, 199_999):
            stream.seek(start)
            assert stream.read(150_000) == content[start : start + 150_000]
        stream.seek(70_000)
        assert stream.read() == content[70_000:]


@pytest.mark.skipif(
    not xz_cli_available(),
    reason="the xz CLI is needed to build a multi-block (single-stream) XZ fixture",
)
def test_xz_block_chain_hands_off_at_a_stream_without_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream whose block scan degraded is decoded, not skipped or cut off.

    Stream B's per-stream scan is made to fail, so the table holds a ``state=None``
    start for B between A's and C's blocks. A resume in A must stop its chain there and
    carry on sequentially: jumping to C would serve C's bytes at B's offsets, and ending
    at A would return a short read and publish a short size.
    """
    from archivey.internal.streams import xz as xz_module

    rng = random.Random(7)
    parts = [rng.randbytes(150_000) for _ in range(3)]
    streams = [make_multiblock_xz(p, block_size=65536) for p in parts]
    compressed = b"".join(streams)
    content = b"".join(parts)
    b_start = len(streams[0])

    real_scan = xz_module._read_xz_index_backwards

    def scan(stream: BinaryIO, file_size: int, stop_at: int = 0, **kw: Any) -> Any:
        if stop_at == b_start:
            raise CorruptionError("forced for the test")
        return real_scan(stream, file_size, stop_at=stop_at, **kw)

    monkeypatch.setattr(xz_module, "_read_xz_index_backwards", scan)

    with XzDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.read() == content
        placeholders = [sp for sp in stream._seek_points[1:] if sp.state is None]
        assert [sp.decompressed_offset for sp in placeholders] == [len(parts[0])]
        stream.seek(70_000)
        assert stream.read(200_000) == content[70_000:270_000]
        stream.seek(70_000)
        assert stream.read() == content[70_000:]
        assert stream.seek(0, io.SEEK_END) == len(content)


def test_xz_truncated_raises() -> None:
    compressed = lzma.compress(CONTENT, format=lzma.FORMAT_XZ)
    with XzDecompressorStream(io.BytesIO(compressed[: len(compressed) // 2])) as stream:
        with pytest.raises(TruncatedError):
            stream.read()


def test_xz_truncated_large_read_recovers_prefix() -> None:
    compressed = lzma.compress(CONTENT, format=lzma.FORMAT_XZ)
    truncated = compressed[: len(compressed) // 2]
    with XzDecompressorStream(io.BytesIO(truncated)) as stream:
        prefix = stream.read(65536)
        assert prefix  # recoverable bytes delivered
        with pytest.raises(TruncatedError):
            stream.read(1)
        stream.close()  # quiet after observed truncation
    with XzDecompressorStream(io.BytesIO(truncated)) as stream:
        with pytest.raises(TruncatedError):
            stream.read()
        assert stream.try_get_size() is None
        with pytest.raises(TruncatedError):
            stream.seek(0, io.SEEK_END)


def test_lzip_truncated_large_read_recovers_prefix() -> None:
    compressed = make_lzip_member(CONTENT)
    truncated = compressed[: len(compressed) // 2]
    with LzipDecompressorStream(io.BytesIO(truncated)) as stream:
        prefix = stream.read(65536)
        assert prefix
        with pytest.raises(TruncatedError):
            stream.read(1)
        stream.close()
    with LzipDecompressorStream(io.BytesIO(truncated)) as stream:
        with pytest.raises(TruncatedError):
            stream.read()
        assert stream.try_get_size() is None
        with pytest.raises(TruncatedError):
            stream.seek(0, io.SEEK_END)


@pytest.mark.skipif(
    not xz_cli_available(),
    reason="the xz CLI is needed for a multi-block first stream before a later stream",
)
def test_xz_partial_index_mid_seek_includes_later_streams() -> None:
    """Stateful resume after indexing only an early stream must not silent-EOF later ones.

    Progressive enrichment adds block-bounds for *completed* streams only. Resuming via
    an ``_XzBlockBounds`` point builds a closed chain from that point plus already-indexed
    later blocks; without a full from-origin index the chain ends at the first stream and
    reads past it return empty. Seek must complete the index first.
    """
    part1 = b"A" * 30_000
    part2 = b"B" * 8_000
    plaintext = part1 + part2
    compressed = make_multiblock_xz(part1, block_size=8192) + lzma.compress(
        part2, format=lzma.FORMAT_XZ
    )
    with XzDecompressorStream(io.BytesIO(compressed), seekable=True) as stream:
        assert stream.read(len(part1)) == part1
        assert not stream._index_built
        # Block-state points exist for stream 1 only at this moment.
        assert any(p.state is not None for p in stream._seek_points)
        mid = len(part1) // 2
        assert stream.seek(mid) == mid
        assert stream._index_built
        # Cross the stream boundary while reading from a mid-stream resume point.
        n = len(part1) - mid + 200
        assert stream.read(n) == plaintext[mid : mid + n]


def test_xz_index_crc_mismatch_raises_on_backwards_scan() -> None:
    """Corrupt index CRC must not be trusted as seek offsets."""
    compressed = bytearray(lzma.compress(CONTENT, format=lzma.FORMAT_XZ))
    # Flip a byte in the index region (just before the 12-byte footer).
    compressed[-16] ^= 0xFF
    with pytest.raises(CorruptionError, match="index CRC32"):
        _read_xz_index_backwards(io.BytesIO(bytes(compressed)), len(compressed))


def test_xz_index_unpadded_overflow_raises() -> None:
    """Index records whose unpadded sizes extend before offset 0 are rejected."""
    from archivey.internal.streams.xz import (
        _XZ_FOOTER_MAGIC,
        _XZ_STREAM_MAGIC,
        _encode_mbi,
        _round_up_4,
    )

    check = 0x00
    flags = bytes([0x00, check])
    header = (
        _XZ_STREAM_MAGIC + flags + struct.pack("<I", zlib.crc32(flags) & 0xFFFFFFFF)
    )
    # Huge unpadded_size → stream_header_start computes negative.
    records = [(1 << 30, 100)]
    body = b"\x00" + _encode_mbi(len(records))
    for unpadded, uncomp in records:
        body += _encode_mbi(unpadded) + _encode_mbi(uncomp)
    body += b"\x00" * (_round_up_4(len(body)) - len(body))
    index = body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)
    backward_raw = (len(index) // 4) - 1
    fbody = struct.pack("<I", backward_raw) + bytes([0x00, check])
    footer = (
        struct.pack("<I", zlib.crc32(fbody) & 0xFFFFFFFF) + fbody + _XZ_FOOTER_MAGIC
    )
    # Minimal fake file: header + tiny pad + index + footer (blocks region empty/wrong).
    blob = header + b"\x00" * 16 + index + footer
    with pytest.raises(CorruptionError, match="negative offset|extends before"):
        _read_xz_index_backwards(io.BytesIO(blob), len(blob))


def test_lzip_trailer_member_size_past_start_raises() -> None:
    """Corrupt member_size that walks before offset 0 must not become seek points."""
    good = make_lzip_member(b"hello-lzip-payload")
    bad = bytearray(good)
    # Trailer: crc32(4) + data_size(8) + member_size(8) at end.
    # Set member_size larger than the file.
    crc, data_size, _member_size = struct.unpack_from("<IQQ", bad, len(bad) - 20)
    struct.pack_into("<IQQ", bad, len(bad) - 20, crc, data_size, len(bad) + 100)
    with pytest.raises(CorruptionError, match="member_size|exceeds"):
        _read_index_backwards(io.BytesIO(bytes(bad)), len(bad))


def _lzip_with_lying_member_size(lying_member: int = 0) -> bytes:
    """Three 256-byte members ``A``/``B``/``C``; one trailer also claims the next member.

    ``lying_member``'s trailer ``member_size`` covers itself and the member after it.
    CRC-32 and ``data_size`` are left intact, so only ``member_size`` lies.
    """
    members = [make_lzip_member(p) for p in (b"A" * 256, b"B" * 256, b"C" * 256)]
    bad = bytearray(b"".join(members))
    trailer_at = sum(len(m) for m in members[: lying_member + 1]) - 20
    crc, data_size, _ = struct.unpack_from("<IQQ", bad, trailer_at)
    claimed = len(members[lying_member]) + len(members[lying_member + 1])
    struct.pack_into("<IQQ", bad, trailer_at, crc, data_size, claimed)
    return bytes(bad)


def test_lzip_trailer_member_size_mismatch_raises_on_forward_read() -> None:
    """A trailer ``member_size`` that disagrees with the bytes consumed is corruption.

    Before this was checked, the forward read accepted the file and recorded the lie as
    the next member's seek point, so ``seek(256)`` then served member 2's bytes.
    """
    bad = _lzip_with_lying_member_size()
    with LzipDecompressorStream(io.BytesIO(bad)) as stream:
        with pytest.raises(CorruptionError, match="member size mismatch"):
            stream.read()


def test_lzip_cold_seek_trusts_a_self_consistent_trailer_chain() -> None:
    """Pins the residual recorded as threat-model O17: an index-only seek trusts trailers.

    Member 1's trailer claims to span members 0 and 1. The backward walk lands on member
    0's real magic and accepts two members, so a seek past the lie, with no full read
    before it, serves member 2's bytes at offset 256 and reads cleanly to the index's
    end. A seek inside the misdescribed region and a full read both raise. If index
    validation is ever added, this test changes with O17.
    """
    bad = _lzip_with_lying_member_size(lying_member=1)

    with LzipDecompressorStream(io.BytesIO(bad)) as stream:
        stream.seek(256)
        assert stream.read() == b"C" * 256  # the index's answer, not member 1
        assert stream.tell() == 512

    with LzipDecompressorStream(io.BytesIO(bad)) as stream:
        with pytest.raises(CorruptionError, match="member size mismatch"):
            stream.seek(100)
            stream.read(16)  # the lying trailer is reached within the first feed

    with LzipDecompressorStream(io.BytesIO(bad)) as stream:
        with pytest.raises(CorruptionError, match="member size mismatch"):
            stream.read()


@pytest.mark.skipif(
    not xz_cli_available(),
    reason="the xz CLI is needed to build a multi-block (single-stream) XZ fixture",
)
def test_xz_cold_seek_trusts_a_self_consistent_block_index() -> None:
    """Pins the xz half of threat-model O17: an index-only seek trusts the stream index.

    Index records 0 and 1 are merged into one record with block 0's uncompressed size.
    The compressed total is unchanged, so the backward scan still finds the stream
    header, and a seek to 65536 with no full read before it serves block C. A seek
    inside the merged region returns correct bytes until the decode reaches the end of
    the stream, where liblzma checks the index and raises; a full read raises too. If
    index validation is ever added, this test changes with O17.
    """
    from archivey.internal.streams import xz as xz_module

    data = b"".join(bytes([ord("A") + i]) * 65536 for i in range(4))
    compressed = make_multiblock_xz(data, block_size=65536)
    footer = compressed[-12:]
    _check, index_size = xz_module._parse_xz_footer(footer)
    index_start = len(compressed) - 12 - index_size
    records = xz_module._parse_xz_index(compressed[index_start : -12 - 4])
    assert len(records) == 4
    (u0, d0), (u1, _d1) = records[0], records[1]
    merged = [(xz_module._round_up_4(u0) + u1, d0), *records[2:]]
    body = b"\x00" + xz_module._encode_mbi(len(merged))
    body += b"".join(
        xz_module._encode_mbi(u) + xz_module._encode_mbi(d) for u, d in merged
    )
    body += b"\x00" * (xz_module._round_up_4(len(body)) - len(body))
    index = body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)
    footer_body = struct.pack("<I", len(index) // 4 - 1) + footer[8:10]
    new_footer = (
        struct.pack("<I", zlib.crc32(footer_body) & 0xFFFFFFFF)
        + footer_body
        + footer[10:]
    )
    bad = compressed[:index_start] + index + new_footer

    with XzDecompressorStream(io.BytesIO(bad)) as stream:
        stream.seek(65536)
        assert stream.read() == data[2 * 65536 :]  # blocks C and D, not B
        assert stream.seek(0, io.SEEK_END) == 3 * 65536

    with XzDecompressorStream(io.BytesIO(bad)) as stream:
        stream.seek(1000)
        assert stream.read(70_000) == data[1000:71_000]  # right bytes, no error yet
        with pytest.raises(CorruptionError):
            stream.read()

    with XzDecompressorStream(io.BytesIO(bad)) as stream:
        with pytest.raises(CorruptionError):
            stream.read()


def test_lzip_multi_member_seek_after_forward_read_serves_the_right_bytes() -> None:
    """Seek points recorded by a forward read land on the member they name."""
    rng = random.Random(6)
    parts = [rng.randbytes(40_000), rng.randbytes(90_000), rng.randbytes(30_000)]
    content = b"".join(parts)
    compressed = make_multi_member_lzip(parts)
    with LzipDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.read() == content
        for start, length in ((40_000, 1000), (135_000, 25_000), (10, 150_000)):
            stream.seek(start)
            assert stream.read(length) == content[start : start + length]


# --- lzip seeking via the trailer scan -------------------------------------------------


def test_lzip_forward_read_roundtrip() -> None:
    compressed = make_lzip_member(CONTENT)
    with LzipDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.read() == CONTENT


def test_lzip_multi_member_roundtrip() -> None:
    compressed = make_multi_member_lzip([b"first-part", b"second-part", b"third"])
    with LzipDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.read() == b"first-partsecond-partthird"


def test_lzip_seek_and_read() -> None:
    compressed = make_lzip_member(CONTENT)
    with LzipDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.seek(12345) == 12345
        assert stream.read(100) == CONTENT[12345:12445]


def test_lzip_seek_end_via_trailer_scan() -> None:
    parts = [b"alpha" * 1000, b"beta" * 1000]
    compressed = make_multi_member_lzip(parts)
    with LzipDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.seek(0, io.SEEK_END) == sum(len(p) for p in parts)


def test_lzip_index_backwards_parses_members() -> None:
    parts = [b"a" * 500, b"b" * 700]
    compressed = make_multi_member_lzip(parts)
    members = _read_index_backwards(io.BytesIO(compressed), len(compressed))
    assert [m.decompressed_size for m in members] == [500, 700]
    assert members[0].decompressed_start == 0
    assert members[1].decompressed_start == 500


# --- accelerator backends present / absent ---------------------------------------------


def test_gzip_accelerator_off_warns_on_rewind_but_still_seeks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With the accelerator OFF, gzip still seeks (slowly); a rewind logs one warning.

    Forward consumption and forward seeks stay quiet — only a backward seek, which
    re-decompresses from the start, triggers the warning.
    """
    config = StreamConfig(use_rapidgzip=AcceleratorMode.OFF, seekable=True)
    compressed = gzip.compress(REWIND_CONTENT)
    with open_codec_stream(Codec.GZIP, io.BytesIO(compressed), config=config) as stream:
        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.read(REWIND_READ) == REWIND_CONTENT[:REWIND_READ]
            assert (
                stream.seek(REWIND_READ + 100) == REWIND_READ + 100
            )  # forward seek: no rewind, no warning
            assert (
                stream.read(10) == REWIND_CONTENT[REWIND_READ + 100 : REWIND_READ + 110]
            )
        assert not caplog.records

        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.seek(0) == 0  # rewind → slow re-decompression
            assert stream.read(10) == REWIND_CONTENT[:10]  # still returns correct data
    assert sum("re-decompresses" in r.getMessage() for r in caplog.records) == 1
    assert any("rapidgzip" in r.getMessage() for r in caplog.records)


def test_bzip2_accelerator_off_warns_on_rewind(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The bz2 stdlib path mirrors gzip: a rewind warns and still seeks."""
    config = StreamConfig(use_indexed_bzip2=AcceleratorMode.OFF, seekable=True)
    compressed = bz2.compress(REWIND_CONTENT)
    with open_codec_stream(
        Codec.BZIP2, io.BytesIO(compressed), config=config
    ) as stream:
        assert stream.read(REWIND_READ) == REWIND_CONTENT[:REWIND_READ]
        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.seek(0) == 0
            assert stream.read(10) == REWIND_CONTENT[:10]
    assert any("rapidgzip" in r.getMessage() for r in caplog.records)


# --- forward-only codecs without an accelerator: warn (generically) on a rewind ---------


def test_zlib_warns_on_rewind(caplog: pytest.LogCaptureFixture) -> None:
    """Stdlib-fallback zlib: a rewind warns and names the ``[seekable]`` accelerator."""
    # Force stdlib (OFF) so the warning path is deterministic regardless of payload size /
    # whether rapidgzip is installed — the accelerator path itself emits no rewind warning.
    compressed = zlib.compress(REWIND_CONTENT)
    config = StreamConfig(use_rapidgzip=AcceleratorMode.OFF, seekable=True)
    with open_codec_stream(Codec.ZLIB, io.BytesIO(compressed), config=config) as stream:
        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.read(REWIND_READ) == REWIND_CONTENT[:REWIND_READ]
            assert (
                stream.seek(REWIND_READ + 100) == REWIND_READ + 100
            )  # forward seek: no warning
            assert (
                stream.read(10) == REWIND_CONTENT[REWIND_READ + 100 : REWIND_READ + 110]
            )
        assert not caplog.records

        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.seek(0) == 0  # rewind → re-decode from start
            assert stream.read(10) == REWIND_CONTENT[:10]  # still correct
    msgs = [r.getMessage() for r in caplog.records]
    assert sum("rapidgzip" in m and "re-decompresses" in m for m in msgs) == 1


def test_deflate_warns_on_rewind(caplog: pytest.LogCaptureFixture) -> None:
    """Stdlib-fallback raw deflate names the rapidgzip accelerator on rewind."""
    co = zlib.compressobj(wbits=-15)
    compressed = co.compress(REWIND_CONTENT) + co.flush()
    config = StreamConfig(use_rapidgzip=AcceleratorMode.OFF, seekable=True)
    with open_codec_stream(
        Codec.DEFLATE, io.BytesIO(compressed), config=config
    ) as stream:
        assert stream.read(REWIND_READ) == REWIND_CONTENT[:REWIND_READ]
        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.seek(0) == 0
            assert stream.read(10) == REWIND_CONTENT[:10]
    assert (
        sum(
            "rapidgzip" in r.getMessage() and "re-decompresses" in r.getMessage()
            for r in caplog.records
        )
        == 1
    )


@requires("brotli")
def test_brotli_warns_on_rewind(caplog: pytest.LogCaptureFixture) -> None:
    import brotli

    compressed = brotli.compress(REWIND_CONTENT)
    with open_codec_stream(
        Codec.BROTLI, io.BytesIO(compressed), config=StreamConfig(seekable=True)
    ) as stream:
        assert stream.read(REWIND_READ) == REWIND_CONTENT[:REWIND_READ]
        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.seek(0) == 0
            assert stream.read(10) == REWIND_CONTENT[:10]
    assert sum("no random-access index" in r.getMessage() for r in caplog.records) == 1


@requires("lz4")
def test_lz4_warns_on_rewind(caplog: pytest.LogCaptureFixture) -> None:
    import lz4.frame

    compressed = lz4.frame.compress(REWIND_CONTENT)
    with open_codec_stream(
        Codec.LZ4, io.BytesIO(compressed), config=StreamConfig(seekable=True)
    ) as stream:
        assert stream.read(REWIND_READ) == REWIND_CONTENT[:REWIND_READ]
        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.seek(0) == 0
            assert stream.read(10) == REWIND_CONTENT[:10]
    assert sum("no random-access index" in r.getMessage() for r in caplog.records) == 1


@requires_zstd()
def test_zstd_rewinds_and_warns_on_backward_seek(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """zstd has no index; a backward seek re-decompresses from the start and warns once."""
    zstd = zstd_backend()
    compressed = zstd.compress(REWIND_CONTENT)
    with open_codec_stream(
        Codec.ZSTD, io.BytesIO(compressed), config=StreamConfig(seekable=True)
    ) as stream:
        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.read(REWIND_READ) == REWIND_CONTENT[:REWIND_READ]
            ahead = REWIND_READ + 300
            assert stream.seek(ahead) == ahead  # forward: no rewind, no warning
            assert stream.read(10) == REWIND_CONTENT[ahead : ahead + 10]
        assert not caplog.records

        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.seek(0) == 0  # backward → re-decode from start
            assert stream.read(REWIND_READ) == REWIND_CONTENT[:REWIND_READ]
    assert sum("no random-access index" in r.getMessage() for r in caplog.records) == 1


def test_gzip_accelerator_on_without_package_raises() -> None:
    """ON explicitly requests rapidgzip; absent, that's a PackageNotInstalledError."""
    if importlib.util.find_spec("rapidgzip") is not None:
        pytest.skip("rapidgzip is installed; cannot exercise the absent path")
    config = StreamConfig(use_rapidgzip=AcceleratorMode.ON)
    compressed = gzip.compress(CONTENT)
    with pytest.raises(PackageNotInstalledError):
        open_codec_stream(Codec.GZIP, io.BytesIO(compressed), config=config).read()


def test_bzip2_accelerator_on_without_package_raises() -> None:
    # bzip2 random access is provided by rapidgzip's bundled IndexedBzip2File, so the absent
    # path is only exercisable when rapidgzip is not installed.
    if importlib.util.find_spec("rapidgzip") is not None:
        pytest.skip("rapidgzip is installed; cannot exercise the absent path")
    config = StreamConfig(use_indexed_bzip2=AcceleratorMode.ON)
    compressed = bz2.compress(CONTENT)
    with pytest.raises(PackageNotInstalledError):
        open_codec_stream(Codec.BZIP2, io.BytesIO(compressed), config=config).read()


def test_accelerator_mode_auto_resolution() -> None:
    """AUTO enables only when seekability is declared and the package is available."""
    assert AcceleratorMode.AUTO.enabled_for(seekable=True, available=True) is True
    assert not AcceleratorMode.AUTO.enabled_for(seekable=False, available=True)
    assert not AcceleratorMode.AUTO.enabled_for(seekable=True, available=False)
    assert AcceleratorMode.ON.enabled_for(seekable=False, available=True)
    assert not AcceleratorMode.OFF.enabled_for(seekable=True, available=True)
    # ON resolves to "use it" even when absent; the opener turns that into a clear
    # PackageNotInstalledError (asserted in the gzip/bzip2 ON-without-package tests).
    assert AcceleratorMode.ON.enabled_for(seekable=True, available=False)
    # AUTO minimum-size gate: known size below min_size falls back; unknown size does not.
    assert not AcceleratorMode.AUTO.enabled_for(
        seekable=True, available=True, input_size=100, min_size=1024
    )
    assert AcceleratorMode.AUTO.enabled_for(
        seekable=True, available=True, input_size=2048, min_size=1024
    )
    assert AcceleratorMode.AUTO.enabled_for(
        seekable=True, available=True, input_size=None, min_size=1024
    )
    # ON ignores the size threshold.
    assert AcceleratorMode.ON.enabled_for(
        seekable=True, available=True, input_size=1, min_size=1024
    )


# --- F5: randomized seek interleaving --------------------------------------------------


hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402


def _seek_interleaving_ops(stream: Any, plaintext: bytes, ops: list[str]) -> None:
    """Drive a random size-probe / seek / read schedule against ``stream``."""
    for op in ops:
        if op == "size":
            size = stream.try_get_size()
            assert size is None or size == len(plaintext)
        elif op == "seek_end":
            assert stream.seek(0, io.SEEK_END) == len(plaintext)
        elif op == "seek0":
            assert stream.seek(0) == 0
        elif op == "read_all":
            stream.seek(0)
            assert stream.read() == plaintext
        elif op == "seek_mid":
            mid = len(plaintext) // 2
            assert stream.seek(mid) == mid
            n = min(32, len(plaintext) - mid)
            assert stream.read(n) == plaintext[mid : mid + n]
        elif op == "read_chunk":
            pos = stream.tell()
            if pos >= len(plaintext):
                stream.seek(0)
                pos = 0
            n = min(64, len(plaintext) - pos)
            assert stream.read(n) == plaintext[pos : pos + n]


def _compress_xz_parts(parts: list[bytes]) -> bytes:
    return make_multi_stream_xz(parts)


def _compress_lzip_parts(parts: list[bytes]) -> bytes:
    return make_multi_member_lzip(parts)


def _compress_unix_parts(parts: list[bytes]) -> bytes:
    # .Z is a single LZW stream (CLEAR seek points inside); join parts first.
    return make_unix_compress(b"".join(parts))


_SEEK_INTERLEAVE_CASES = [
    pytest.param(
        _compress_xz_parts,
        XzDecompressorStream,
        id="xz",
    ),
    pytest.param(
        _compress_lzip_parts,
        LzipDecompressorStream,
        id="lzip",
    ),
    pytest.param(
        _compress_unix_parts,
        UnixCompressDecompressorStream,
        id="unix_compress",
        marks=requires("ncompress"),
    ),
]


@pytest.mark.parametrize("compress_parts,stream_cls", _SEEK_INTERLEAVE_CASES)
@given(
    parts=st.lists(
        st.binary(min_size=16, max_size=400),
        min_size=1,
        max_size=4,
    ),
    ops=st.lists(
        st.sampled_from(
            ["size", "seek_end", "seek0", "read_all", "seek_mid", "read_chunk"]
        ),
        min_size=1,
        max_size=12,
    ),
)
def test_seek_interleaving_matches_plaintext(
    compress_parts: Callable[[list[bytes]], bytes],
    stream_cls: Callable[..., BinaryIO],
    parts: list[bytes],
    ops: list[str],
) -> None:
    """Random size-probe / seek / read order must match plaintext; no raw asserts (F5).

    Covers XZ (multi-stream index), lzip (trailer-scan members), and unix-compress
    (CLEAR seek points). Includes mid-seeks after only a partial forward read: stateful
    resume must force a complete from-origin index so later streams are not silently
    truncated.
    """
    plaintext = b"".join(parts)
    compressed = compress_parts(parts)
    with stream_cls(io.BytesIO(compressed), seekable=True) as stream:
        _seek_interleaving_ops(stream, plaintext, ops)
