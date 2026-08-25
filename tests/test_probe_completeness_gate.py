"""Completeness gate + Brotli chain walk (probe-completeness-gate)."""

from __future__ import annotations

import io
import os
import zlib
from pathlib import Path

import pytest

from archivey import (
    ArchiveFormat,
    DetectionConfidence,
    detect_format,
)
from archivey.exceptions import FormatDetectionError
from archivey.internal.streams.brotli_framing import (
    CHAIN_MAX_LINKS,
    BrotliFirstBlock,
    chain_proves_invalid,
    first_block_overruns_source,
    parse_first_metablock,
    parse_metablock,
)
from archivey.internal.streams.codecs import BrotliCodec, LzmaAloneCodec, ZlibCodec
from archivey.internal.streams.peekable import DETECTION_LIMIT, PeekableStream
from tests.conftest import requires
from tests.streams_util import NonSeekableBytesIO


def _compressed_second_header() -> bytes:
    """A non-first meta-block header that classifies as COMPRESSED (walk stops)."""
    for seed in range(4096):
        hdr = bytes([(seed + j * 13) % 256 for j in range(24)])
        if parse_metablock(hdr, first=False).outcome is BrotliFirstBlock.COMPRESSED:
            return hdr
    raise RuntimeError("no compressed second-header pattern found")


def _guess_residual_surviving_chain() -> bytes:
    """Uncompressed-first fabrication that passes framing + chain, fails full decode.

    The historical ``/**\\n`` + padding residual is rejected by the chain walk once the
    trailing bytes are examined. Replace the second link with a compressed header so the
    walk stops, matching the OLE/COFF residual shape while keeping Alone out of the way.
    """
    framing = parse_first_metablock(b"/**\n")
    assert framing.declares_length
    assert framing.consumed is not None and framing.declared_length is not None
    return (
        b"/**\n"
        + b"x" * framing.declared_length
        + _compressed_second_header()
        + b"Z" * 32
    )


@requires("brotli")
def test_small_real_brotli_survives_completeness() -> None:
    """A complete stream that fits in the prefix must still be accepted."""
    import brotli

    for payload in (b"", b"hello", b"x" * 50, b"payload " * 10):
        data = brotli.compress(payload)
        assert len(data) <= DETECTION_LIMIT
        assert BrotliCodec().content_probe(data, source_length=len(data)) is True
        info = detect_format(io.BytesIO(data))
        assert info.format == ArchiveFormat.BROTLI


@requires("brotli")
def test_completeness_rejects_tiny_nonterminating_file() -> None:
    blob = b"hello"
    assert parse_first_metablock(blob).outcome is BrotliFirstBlock.COMPRESSED
    assert BrotliCodec().content_probe(blob, source_length=len(blob)) is False
    with pytest.raises(FormatDetectionError):
        detect_format(io.BytesIO(blob))


@requires("brotli")
def test_completeness_skipped_when_source_larger_than_prefix() -> None:
    import brotli

    data = brotli.compress(os.urandom(64 * 1024))
    assert len(data) > DETECTION_LIMIT
    prefix = data[:DETECTION_LIMIT]
    assert BrotliCodec().content_probe(prefix, source_length=len(data)) is True


@requires("brotli")
def test_real_brotli_corpus_includes_sub_100_byte_payloads() -> None:
    import brotli

    payloads = [b"", b"hello", b"x", b"a" * 9, b"payload under 100 bytes here"]
    for quality in (0, 1, 5, 9, 11):
        for lgwin in (10, 22, 24):
            for payload in payloads:
                try:
                    data = brotli.compress(payload, quality=quality, lgwin=lgwin)
                except brotli.error:
                    continue
                info = detect_format(io.BytesIO(data))
                assert info.format == ArchiveFormat.BROTLI, (
                    f"missed q={quality} lgwin={lgwin} len={len(payload)}"
                )


def test_zlib_completeness_rejects_fully_visible_nonterminating() -> None:
    # Recognized CMF/FLG plus bytes that start a deflate block but never finish.
    blob = b"\x78\x9c\x01\x00\x00"
    assert ZlibCodec().content_probe(blob, source_length=len(blob)) is False


def test_zlib_complete_small_stream_still_accepted() -> None:
    data = zlib.compress(b"zlib payload")
    assert detect_format(io.BytesIO(data)).format == ArchiveFormat.ZLIB
    assert ZlibCodec().content_probe(data, source_length=len(data)) is True


def test_lzma_alone_completeness_on_fully_visible_nonterminating() -> None:
    alone = LzmaAloneCodec()
    header = bytes([0x5D, 0x00, 0x00, 0x01, 0x00]) + b"\xff" * 8
    blob = header + b"\x00" * 40
    # When the bounded sample matches but the whole source is visible and does not
    # terminate, completeness must reject.
    if alone.content_probe(blob, source_length=None):
        assert alone.content_probe(blob, source_length=len(blob)) is False


@requires("brotli")
def test_chain_walk_rejects_second_link_overrun() -> None:
    framing = parse_first_metablock(b"/**\n")
    assert framing.consumed is not None and framing.declared_length is not None
    # Source long enough for the first block, too short for a second declaring MLEN.
    source_length = framing.consumed + framing.declared_length + 8
    prefix = (b"/**\n" + b"x" * framing.declared_length)[:DETECTION_LIMIT]
    overrun_hdr = b"MZ" + b"\x90" * 22

    def read_at(offset: int, length: int) -> bytes | None:
        if offset + length <= len(prefix):
            return prefix[offset : offset + length]
        return overrun_hdr[:length]

    assert chain_proves_invalid(prefix, source_length, read_at=read_at) is True


@requires("brotli")
def test_chain_walk_rejects_trailing_bytes_after_declared_end() -> None:
    assert parse_first_metablock(b"\x06").outcome is BrotliFirstBlock.EMPTY_LAST
    # Prefix must be long enough for the header read; trailing bytes past consumed=1.
    blob = b"\x06" + b"\x00" * 40
    assert chain_proves_invalid(blob, len(blob)) is True


@requires("brotli")
def test_chain_walk_link_cap_does_not_reject() -> None:
    # Every link is a tiny non-last uncompressed block; hitting the link cap must not
    # reject (cannot disprove).
    small = None
    info = None
    for seed in range(256):
        cand = bytes([seed]) + b"\x00" * 23
        got = parse_first_metablock(cand)
        if (
            got.outcome is BrotliFirstBlock.UNCOMPRESSED
            and got.declared_length is not None
            and got.declared_length <= 4
            and got.consumed is not None
            and not got.is_last
        ):
            small, info = cand, got
            break
    if small is None or info is None:
        pytest.skip("could not synthesize repeating chain links")
    step = info.consumed + info.declared_length
    source_length = step * (CHAIN_MAX_LINKS + 4)

    def read_at(offset: int, length: int) -> bytes | None:
        return small[:length]

    assert (
        chain_proves_invalid(
            small, source_length, read_at=read_at, max_links=CHAIN_MAX_LINKS
        )
        is False
    )


@requires("brotli")
def test_chain_walk_stops_immediately_on_compressed_first() -> None:
    import brotli

    data = brotli.compress(b"payload " * 40)
    assert parse_first_metablock(data).outcome is BrotliFirstBlock.COMPRESSED
    reads: list[tuple[int, int]] = []

    def read_at(offset: int, length: int) -> bytes | None:
        reads.append((offset, length))
        return data[offset : offset + length]

    assert chain_proves_invalid(data[:4], len(data), read_at=read_at) is False
    # May extend the first-header read past a short prefix; must not walk to a later link.
    assert all(off < 24 for off, _ in reads)


@requires("brotli")
def test_ole_coff_residuals_still_accepted_above_prefix() -> None:
    ole = bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 8000
    assert len(ole) > DETECTION_LIMIT
    assert first_block_overruns_source(ole, len(ole)) is False
    prefix = ole[:DETECTION_LIMIT]

    def read_at(offset: int, length: int) -> bytes | None:
        return ole[offset : offset + length]

    assert (
        BrotliCodec().content_probe(prefix, source_length=len(ole), read_at=read_at)
        is True
    )
    ole_info = detect_format(io.BytesIO(ole))
    assert ole_info.format in (ArchiveFormat.LZMA_ALONE, ArchiveFormat.BROTLI)

    coff_header = bytes.fromhex("6486100100")
    framing = parse_first_metablock(coff_header)
    assert framing.outcome is BrotliFirstBlock.UNCOMPRESSED
    assert framing.consumed is not None and framing.declared_length is not None
    second = _compressed_second_header()
    coff = (
        coff_header
        + b"\x00" * (framing.consumed + framing.declared_length - len(coff_header))
        + second
        + b"\x00" * 8
    )
    assert first_block_overruns_source(coff, len(coff)) is False
    coff_info = detect_format(io.BytesIO(coff))
    assert coff_info.format in (ArchiveFormat.LZMA_ALONE, ArchiveFormat.BROTLI)


@requires("brotli")
def test_unknown_length_keeps_today_behaviour_for_both_rules() -> None:
    blob = b"MZ" + b"\x90" * 8000
    assert BrotliCodec().content_probe(blob, source_length=None) is True
    assert BrotliCodec().content_probe(blob, source_length=len(blob)) is False


@requires("brotli")
def test_nonseekable_unknown_length_skips_both_rules() -> None:
    stub = b"MZ" + b"\x90" * 4094
    info = detect_format(PeekableStream(NonSeekableBytesIO(stub)))
    assert info.format == ArchiveFormat.BROTLI


@requires("brotli")
def test_sixteen_mib_vacuous_first_block_caught_by_walk(tmp_path: Path) -> None:
    """MLEN ceiling makes the first-block check vacuous; the walk still decides."""
    framing = parse_first_metablock(b"/**\n")
    assert framing.consumed is not None and framing.declared_length is not None
    # 16 MiB source: first declaring block fits trivially; second link overruns.
    size = 16 * 1024 * 1024
    assert framing.consumed + framing.declared_length <= size
    assert first_block_overruns_source(b"/**\n", size) is False
    path = tmp_path / "big.bin"
    with path.open("wb") as f:
        f.write(b"/**\n")
        f.write(b"\x00" * framing.declared_length)
        f.write(b"MZ" + b"\x90" * 22)
        f.seek(size - 1)
        f.write(b"\x00")

    def read_at(offset: int, length: int) -> bytes | None:
        with path.open("rb") as f:
            f.seek(offset)
            return f.read(length)

    assert chain_proves_invalid(b"/**\n" + b"\x00" * 20, size, read_at=read_at) is True
    assert (
        BrotliCodec().content_probe(
            (b"/**\n" + b"\x00" * 100)[:DETECTION_LIMIT],
            source_length=size,
            read_at=read_at,
        )
        is False
    )


@requires("brotli")
def test_guess_residual_surviving_chain_is_still_guess() -> None:
    blob = _guess_residual_surviving_chain()
    info = detect_format(io.BytesIO(blob))
    assert info.format == ArchiveFormat.BROTLI
    assert info.confidence == DetectionConfidence.GUESS
