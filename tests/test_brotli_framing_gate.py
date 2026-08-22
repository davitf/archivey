"""Brotli framing gate + probe-only GUESS provenance (brotli-probe-framing-gate)."""

from __future__ import annotations

import io
import zlib
from pathlib import Path

import pytest

from archivey import (
    ArchiveFormat,
    DetectionConfidence,
    detect_format,
    open_archive,
)
from archivey.diagnostics import DiagnosticCode
from archivey.exceptions import (
    CorruptionError,
    FormatDetectionError,
    TruncatedError,
)
from archivey.internal.streams.brotli_framing import (
    BrotliFirstBlock,
    first_block_overruns_source,
    parse_first_metablock,
)
from archivey.internal.streams.codecs import BrotliCodec, LzmaAloneCodec
from tests.conftest import requires


@requires("brotli")
def test_partial_output_then_error_on_fitting_uncompressed_prefix() -> None:
    # First uncompressed block fits; decoder may copy a buffer of input before failing.
    framing = parse_first_metablock(b"/**\n")
    assert framing.consumed is not None and framing.declared_length is not None
    need = framing.consumed + framing.declared_length
    blob = b"/**\n" + b"x" * (need + 64)
    with open_archive(io.BytesIO(blob)) as reader:
        member = next(iter(reader))
        stream = reader.open(member)
        chunk = stream.read(65536)
        # Either we already got bytes, or the next read raises — both are the
        # bytes-then-error shape the provenance wording must not deny.
        if chunk:
            assert len(chunk) > 0
            with pytest.raises((TruncatedError, CorruptionError)) as caught:
                while stream.read(65536):
                    pass
            assert caught.value.format_unconfirmed is True
        else:
            with pytest.raises((TruncatedError, CorruptionError)) as caught:
                stream.read(1)
            assert caught.value.format_unconfirmed is True


@requires("brotli")
def test_real_brotli_corpus_zero_false_negatives() -> None:
    """Qualities × lgwin × payloads including incompressible — binding FN constraint."""
    import os

    import brotli

    payloads = [
        b"",
        b"x",
        b"hello world",
        bytes(range(256)),
        b"\xff" * 4096,
        os.urandom(64 * 1024),
    ]
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


@requires("brotli")
def test_real_brotli_compressed_first_is_probable_without_extension() -> None:
    import brotli

    data = brotli.compress(b"payload " * 40)
    assert parse_first_metablock(data).outcome is BrotliFirstBlock.COMPRESSED
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat.BROTLI
    assert info.confidence == DetectionConfidence.PROBABLE
    assert info.detected_by == "content_probe"


@requires("brotli")
def test_real_brotli_with_br_extension_is_probable(tmp_path: Path) -> None:
    import brotli

    path = tmp_path / "x.br"
    path.write_bytes(brotli.compress(b"hello"))
    info = detect_format(path)
    assert info.format == ArchiveFormat.BROTLI
    assert info.confidence == DetectionConfidence.PROBABLE


@requires("brotli")
def test_framing_gate_rejects_mz_stub_and_doxygen_opener() -> None:
    for blob in (b"MZ" + b"\x90" * 4094, b"/**\n" + b" " * 200):
        assert first_block_overruns_source(blob, len(blob))
        with pytest.raises(FormatDetectionError):
            detect_format(io.BytesIO(blob))


@requires("brotli")
def test_framing_gate_rejects_random_overrunning_blobs() -> None:
    # A short random sweep: anything the gate rejects must not detect as BROTLI.
    # (Acceptance after the gate is the residual — tested separately.)
    rejected = 0
    for i in range(200):
        blob = bytes((i * 37 + j * 13) % 256 for j in range(256))
        if first_block_overruns_source(blob, len(blob)):
            rejected += 1
            with pytest.raises(FormatDetectionError):
                detect_format(io.BytesIO(blob))
    assert rejected > 0


def test_lzma_alone_rejects_header_only_source() -> None:
    alone = LzmaAloneCodec()
    assert alone.content_probe(b"cryptography\n", source_length=13) is False
    with pytest.raises(FormatDetectionError):
        detect_format(io.BytesIO(b"cryptography\n"))


@requires("brotli")
def test_ole_survives_brotli_framing_gate_directly() -> None:
    # Named residual: OLE/CFB magic declares MLEN 7422, so the first-block gate always
    # fits for files ≥ 7425 bytes. Alone may win detect_format probe order; the Brotli
    # gate itself must still accept — sound, not exhaustive.
    ole = bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 8000
    assert first_block_overruns_source(ole, len(ole)) is False
    assert BrotliCodec().content_probe(ole, source_length=len(ole)) is True


@requires("brotli")
def test_brotli_residual_that_fits_framing_detects_as_guess() -> None:
    # ``/**\n`` declares ~283 KiB; pad past that so the first-block check passes.
    # Alone rejects these bytes, so detect_format reaches the Brotli probe.
    framing = parse_first_metablock(b"/**\n")
    assert framing.declares_length
    assert framing.consumed is not None and framing.declared_length is not None
    need = framing.consumed + framing.declared_length
    blob = b"/**\n" + b"x" * (need + 64)
    info = detect_format(io.BytesIO(blob))
    assert info.format == ArchiveFormat.BROTLI
    assert info.confidence == DetectionConfidence.GUESS
    assert info.detected_by == "content_probe"


@requires("brotli")
def test_guess_decode_failure_sets_format_unconfirmed() -> None:
    framing = parse_first_metablock(b"/**\n")
    assert framing.consumed is not None and framing.declared_length is not None
    need = framing.consumed + framing.declared_length
    blob = b"/**\n" + b"x" * (need + 64)
    with open_archive(io.BytesIO(blob)) as reader:
        member = next(iter(reader))
        with pytest.raises((TruncatedError, CorruptionError)) as caught:
            reader.open(member).read()
        exc = caught.value
        assert exc.format_unconfirmed is True
        assert "unconfirmed" in exc.message.lower()
        assert "Partial output may already have been produced" in exc.raw_message
        assert "format_unconfirmed=True" in str(exc)
        codes = {d.code for d in reader.diagnostics.retained}
        assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED in codes


@requires("brotli")
def test_probable_br_decode_failure_does_not_set_unconfirmed(tmp_path: Path) -> None:
    # Truncated real Brotli with .br extension: format is corroborated.
    import brotli

    path = tmp_path / "x.br"
    full = brotli.compress(b"enough payload " * 200)
    path.write_bytes(full[: max(8, len(full) // 3)])
    with open_archive(path) as reader:
        member = next(iter(reader))
        with pytest.raises((TruncatedError, CorruptionError)) as caught:
            reader.open(member).read()
        assert caught.value.format_unconfirmed is False
        assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED not in {
            d.code for d in reader.diagnostics.retained
        }


@requires("brotli")
def test_unknown_length_skips_framing_gate() -> None:
    # Without a known length the gate must not reject; MZ+0x90 is accepted by the
    # decode sample today and must stay accepted when source_length is omitted.
    blob = b"MZ" + b"\x90" * 8000
    assert BrotliCodec().content_probe(blob, source_length=None) is True
    assert BrotliCodec().content_probe(blob, source_length=len(blob)) is False


def test_zlib_probe_ignores_source_length() -> None:
    data = zlib.compress(b"zlib payload")
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat.ZLIB
    assert info.confidence == DetectionConfidence.PROBABLE


def test_first_metablock_parser_vectors() -> None:
    # Contrast pair from the investigation: MZ+0x90 is uncompressed; MZ+0x00 / MZ+A reject.
    mz90 = parse_first_metablock(b"MZ\x90\x90")
    assert mz90.outcome is BrotliFirstBlock.UNCOMPRESSED
    assert mz90.declared_length == 2_171_061
    assert parse_first_metablock(b"MZ\x00\x00").outcome is BrotliFirstBlock.UNDECIDED
    assert parse_first_metablock(b"MZA\x00").outcome is BrotliFirstBlock.UNDECIDED
    assert parse_first_metablock(b"/**\n").outcome is BrotliFirstBlock.UNCOMPRESSED
