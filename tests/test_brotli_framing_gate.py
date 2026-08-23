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
    # First uncompressed block fits; decoder copies a full buffer before failing.
    framing = parse_first_metablock(b"/**\n")
    assert framing.consumed is not None and framing.declared_length is not None
    need = framing.consumed + framing.declared_length
    blob = b"/**\n" + b"x" * (need + 64)
    with open_archive(io.BytesIO(blob)) as reader:
        member = next(iter(reader))
        stream = reader.open(member)
        chunk = stream.read(65536)
        assert len(chunk) == 65536
        with pytest.raises((TruncatedError, CorruptionError)) as caught:
            while stream.read(65536):
                pass
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
def test_ole_and_coff_residuals_honest_detect_format() -> None:
    # Named residual families survive the Brotli first-block gate, but end-to-end
    # detect_format claims them via LZMA Alone at PROBABLE (probe order) — so the
    # Brotli GUESS / format_unconfirmed channel does not cover them (task 5.8).
    ole = bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 8000
    assert first_block_overruns_source(ole, len(ole)) is False
    assert BrotliCodec().content_probe(ole, source_length=len(ole)) is True
    ole_info = detect_format(io.BytesIO(ole))
    assert ole_info.format == ArchiveFormat.LZMA_ALONE
    assert ole_info.confidence == DetectionConfidence.PROBABLE

    # COFF AMD64 machine word + crafted trailer that is a fitting uncompressed Brotli
    # first block (IMAGE_FILE_MACHINE_AMD64 = 0x8664 little-endian).
    coff_header = bytes.fromhex("6486100100")
    framing = parse_first_metablock(coff_header)
    assert framing.outcome is BrotliFirstBlock.UNCOMPRESSED
    assert framing.consumed is not None and framing.declared_length is not None
    coff = coff_header + b"\x00" * (
        framing.consumed + framing.declared_length - len(coff_header)
    )
    assert first_block_overruns_source(coff, len(coff)) is False
    assert BrotliCodec().content_probe(coff, source_length=len(coff)) is True
    coff_info = detect_format(io.BytesIO(coff))
    assert coff_info.format == ArchiveFormat.LZMA_ALONE
    assert coff_info.confidence == DetectionConfidence.PROBABLE


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


@requires("brotli")
def test_nonseekable_pipe_skips_gate_at_detection_limit() -> None:
    # End-to-end: short non-seekable peeks still get a length (peek came back short);
    # at DETECTION_LIMIT the length is unknown and the A-34 stub remains a probe hit.
    from archivey.internal.streams.peekable import PeekableStream
    from tests.streams_util import NonSeekableBytesIO

    stub = b"MZ" + b"\x90" * 4094
    short = stub[:3000]
    with pytest.raises(FormatDetectionError):
        detect_format(PeekableStream(NonSeekableBytesIO(short)))
    info = detect_format(PeekableStream(NonSeekableBytesIO(stub)))
    assert info.format == ArchiveFormat.BROTLI


@requires("brotli")
def test_pedantic_keeps_typed_error_on_probe_unconfirmed() -> None:
    from archivey import ArchiveyConfig, DiagnosticPolicy
    from archivey.exceptions import DiagnosticRaisedError

    framing = parse_first_metablock(b"/**\n")
    assert framing.consumed is not None and framing.declared_length is not None
    need = framing.consumed + framing.declared_length
    blob = b"/**\n" + b"x" * (need + 64)
    cfg = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.pedantic())
    with open_archive(io.BytesIO(blob), config=cfg) as reader:
        member = next(iter(reader))
        with pytest.raises((TruncatedError, CorruptionError)) as caught:
            reader.open(member).read()
        assert not isinstance(caught.value, DiagnosticRaisedError)
        assert caught.value.format_unconfirmed is True
        assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED in {
            d.code for d in reader.diagnostics.retained
        }


@requires("brotli")
def test_probe_unconfirmed_diagnostic_emitted_once_across_retries() -> None:
    framing = parse_first_metablock(b"/**\n")
    assert framing.consumed is not None and framing.declared_length is not None
    need = framing.consumed + framing.declared_length
    blob = b"/**\n" + b"x" * (need + 64)
    with open_archive(io.BytesIO(blob)) as reader:
        stream = reader.open(next(iter(reader)))
        for _ in range(3):
            with pytest.raises((TruncatedError, CorruptionError)) as caught:
                stream.read()
            assert caught.value.format_unconfirmed is True
        assert reader.diagnostics.counts[DiagnosticCode.PROBE_FORMAT_UNCONFIRMED] == 1


@requires("brotli")
def test_probe_unconfirmed_dedup_holds_under_a_raising_policy() -> None:
    # The dedup bound covers counting/retention, and a RAISE policy must not smuggle a
    # second record in through the escalating emit. Each retry still raises the typed
    # error, which is what stops a caller who asked to be stopped.
    from archivey import ArchiveyConfig, DiagnosticPolicy
    from archivey.exceptions import DiagnosticRaisedError

    framing = parse_first_metablock(b"/**\n")
    assert framing.consumed is not None and framing.declared_length is not None
    need = framing.consumed + framing.declared_length
    blob = b"/**\n" + b"x" * (need + 64)
    cfg = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.pedantic())
    with open_archive(io.BytesIO(blob), config=cfg) as reader:
        stream = reader.open(next(iter(reader)))
        for _ in range(3):
            with pytest.raises((TruncatedError, CorruptionError)) as caught:
                stream.read()
            assert not isinstance(caught.value, DiagnosticRaisedError)
            assert caught.value.format_unconfirmed is True
        assert reader.diagnostics.counts[DiagnosticCode.PROBE_FORMAT_UNCONFIRMED] == 1
        assert (
            len(
                [
                    d
                    for d in reader.diagnostics.retained
                    if d.code is DiagnosticCode.PROBE_FORMAT_UNCONFIRMED
                ]
            )
            == 1
        )


@requires("brotli")
def test_probe_unconfirmed_context_carries_detected_format() -> None:
    framing = parse_first_metablock(b"/**\n")
    assert framing.consumed is not None and framing.declared_length is not None
    need = framing.consumed + framing.declared_length
    blob = b"/**\n" + b"x" * (need + 64)
    with open_archive(io.BytesIO(blob)) as reader:
        with pytest.raises((TruncatedError, CorruptionError)):
            reader.open(next(iter(reader))).read()
        probe_diags = [
            d
            for d in reader.diagnostics.retained
            if d.code is DiagnosticCode.PROBE_FORMAT_UNCONFIRMED
        ]
        assert len(probe_diags) == 1
        ctx = probe_diags[0].context
        assert ctx.chosen_by == "content_probe"
        assert ctx.format == "BROTLI"
        assert ctx.detected_format == "BROTLI"


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


def test_first_metablock_matches_survey_self_test_vectors() -> None:
    """Keep ``brotli_framing`` aligned with the exploration script's SELF_TEST_VECTORS."""
    # Outcomes the survey names that map onto BrotliFirstBlock (exuberant / bad-pad →
    # UNDECIDED). WBITS / MLEN checked where the survey asserts them.
    cases = [
        (b"MZ" + b"\x90" * 254, BrotliFirstBlock.UNCOMPRESSED, 2_171_061),
        (b"MZ" + b"\x00" * 254, BrotliFirstBlock.UNDECIDED, None),
        (b"MZ" + b"A" * 254, BrotliFirstBlock.COMPRESSED, None),
        (b"\x7fELF" + b"\x00" * 60, BrotliFirstBlock.UNDECIDED, None),
        (b"\x06" + b"\x00" * 60, BrotliFirstBlock.EMPTY_LAST, None),
        (b"MZ\x90\x00" + b"\x90" * 252, BrotliFirstBlock.UNDECIDED, None),
    ]
    for data, want_outcome, want_mlen in cases:
        got = parse_first_metablock(data)
        assert got.outcome is want_outcome, data[:6]
        if want_mlen is not None:
            assert got.declared_length == want_mlen
