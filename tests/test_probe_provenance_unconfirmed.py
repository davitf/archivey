"""Provenance-based ``format_unconfirmed`` (probe-provenance-unconfirmed).

The honesty channel stamps a decode failure when a content probe was the *sole*
evidence — at any ``DetectionConfidence`` — and leaves corroborated hits alone.
"""

from __future__ import annotations

import io
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from archivey import (
    ArchiveFormat,
    ArchiveyConfig,
    DecoderLimits,
    DetectionConfidence,
    DiagnosticPolicy,
    detect_format,
    open_archive,
)
from archivey.detection_cost import BALANCED_BUDGET
from archivey.diagnostics import Diagnostic, DiagnosticCode
from archivey.exceptions import (
    CorruptionError,
    DiagnosticRaisedError,
    ResourceLimitError,
    TruncatedError,
)
from archivey.internal.detection import _extension_corroborates
from archivey.internal.streams.brotli_framing import BrotliBlock, parse_metablock
from archivey.types import ContainerFormat, StreamFormat
from tests.conftest import requires

TAR_BROTLI = ArchiveFormat(ContainerFormat.TAR, StreamFormat.BROTLI)


def _open_and_read(
    source: Path | io.BytesIO,
    diagnostics: list[Diagnostic] | None = None,
    *,
    config: ArchiveyConfig | None = None,
) -> None:
    """Open ``source`` and read its first member, collecting every diagnostic.

    A single-file source that does not decode raises from ``open_archive`` itself, so
    there is no reader left to ask for ``diagnostics``; ``on_diagnostic`` sees them
    wherever the failure lands.
    """
    config = config or ArchiveyConfig()
    if diagnostics is not None:
        config = replace(config, on_diagnostic=diagnostics.append)
    with open_archive(source, config=config) as reader:
        reader.open(next(iter(reader))).read()


def _probable_brotli_probe_only_residual() -> bytes:
    """A real Brotli stream cut short, larger than the completion window, no extension cue.

    The probe sees a valid compressed-first stream in its window and reports
    ``BROTLI`` / ``PROBABLE`` / ``content_probe``; the source is too large for the
    whole-source completion check, so only the read finds the truncation. Text that
    merely decodes for a while no longer serves: the 4 KiB probe sample and the
    completion check both reject it.
    """
    import random

    import brotli

    text = random.Random(0).randbytes(200_000).hex().encode()
    compressed = brotli.compress(text)
    target = BALANCED_BUDGET.completion_window_bytes + 4096
    assert len(compressed) > target
    blob = compressed[:target]
    assert parse_metablock(blob).outcome is BrotliBlock.COMPRESSED
    return blob


def _ole_lzma_alone_residual() -> bytes:
    """OLE/CFB header padded past the detection peek — Alone at ``PROBABLE``."""
    return bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 8000


def _chain_surviving_guess_residual() -> bytes:
    """Uncompressed-first FP that passes framing + chain (compressed second link)."""
    from tests.streams_util import brotli_compressed_metablock_header

    framing = parse_metablock(b"/**\n")
    assert framing.consumed is not None and framing.declared_length is not None
    second = brotli_compressed_metablock_header(first=False)
    return b"/**\n" + b"x" * framing.declared_length + second + b"Z" * 32


@requires("brotli")
def test_compressed_first_probable_failure_sets_format_unconfirmed() -> None:
    blob = _probable_brotli_probe_only_residual()
    info = detect_format(io.BytesIO(blob))
    assert info.format == ArchiveFormat.BROTLI
    assert info.confidence == DetectionConfidence.PROBABLE
    assert info.detected_by == "content_probe"
    assert info.corroborated is False

    diagnostics: list[Diagnostic] = []
    with pytest.raises((TruncatedError, CorruptionError)) as caught:
        _open_and_read(io.BytesIO(blob), diagnostics)
    exc = caught.value
    assert exc.format_unconfirmed is True
    assert "unconfirmed" in exc.message.lower()
    assert "Partial output may already have been produced" in exc.raw_message
    assert "GUESS" not in exc.raw_message
    messages = " ".join(d.message for d in diagnostics)
    assert "GUESS" not in messages
    assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED in {d.code for d in diagnostics}


def test_lzma_alone_probable_failure_sets_format_unconfirmed() -> None:
    blob = _ole_lzma_alone_residual()
    info = detect_format(io.BytesIO(blob))
    assert info.format == ArchiveFormat.LZMA_ALONE
    assert info.confidence == DetectionConfidence.PROBABLE
    assert info.detected_by == "content_probe"
    assert info.corroborated is False

    # The OLE header's bytes 1-4 read as a 2.7 GiB dictionary, over the default cap;
    # this case lifts the cap to reach the decode failure, and the next one keeps it.
    config = ArchiveyConfig(decoder_limits=DecoderLimits.UNLIMITED)
    diagnostics: list[Diagnostic] = []
    with pytest.raises((TruncatedError, CorruptionError)) as caught:
        _open_and_read(io.BytesIO(blob), diagnostics, config=config)
    assert caught.value.format_unconfirmed is True
    codes = {d.code for d in diagnostics}
    assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED in codes
    assert DiagnosticCode.EXTENSION_FORMAT_UNCONFIRMED not in codes


def test_lzma_alone_probable_limit_refusal_sets_format_unconfirmed() -> None:
    """A decoder-limit refusal on probe-only evidence is stamped like a decode failure.

    The dictionary the refusal names is four bytes of an OLE header, so the ordinary
    "raise the cap if the archive is trusted" advice would be about a file that was
    never ``.lzma``.
    """
    blob = _ole_lzma_alone_residual()
    diagnostics: list[Diagnostic] = []
    with pytest.raises(ResourceLimitError) as caught:
        _open_and_read(io.BytesIO(blob), diagnostics)
    assert caught.value.format_unconfirmed is True
    message = str(caught.value)
    assert "unconfirmed" in message
    # Nothing was decoded: the refusal comes before any decoder is built.
    assert "stopped by a limit" in message
    assert "Decode failed" not in message
    assert "Partial output" not in message
    codes = {d.code for d in diagnostics}
    assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED in codes


def test_lzma_alone_limit_refusal_with_extension_is_not_stamped(
    tmp_path: Path,
) -> None:
    path = tmp_path / "x.lzma"
    path.write_bytes(_ole_lzma_alone_residual())
    with pytest.raises(ResourceLimitError) as caught:
        _open_and_read(path)
    assert caught.value.format_unconfirmed is False


@requires("brotli")
def test_br_extension_failure_does_not_stamp(tmp_path: Path) -> None:
    # Same residual as the probe-only case, but ``.br`` corroborates the claim.
    path = tmp_path / "x.br"
    path.write_bytes(_probable_brotli_probe_only_residual())
    info = detect_format(path)
    assert info.format == ArchiveFormat.BROTLI
    assert info.detected_by == "content_probe"
    assert info.corroborated is True
    diagnostics: list[Diagnostic] = []
    with pytest.raises((TruncatedError, CorruptionError)) as caught:
        _open_and_read(path, diagnostics)
    assert caught.value.format_unconfirmed is False
    assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED not in {d.code for d in diagnostics}


@requires("brotli")
def test_deferred_tar_br_extension_corroborates(tmp_path: Path) -> None:
    """``foo.tar.br`` over a bare-Brotli payload: the extension still agrees on the codec.

    The inner-TAR probe finds no tar, so detection reports bare ``BROTLI`` while the
    extension says ``TAR_BROTLI`` — the documented deferred case, not a conflict.
    """
    path = tmp_path / "x.tar.br"
    path.write_bytes(_probable_brotli_probe_only_residual())
    info = detect_format(path)
    assert info.format == ArchiveFormat.BROTLI
    assert info.detected_by == "content_probe"
    assert info.corroborated is True

    with pytest.raises((TruncatedError, CorruptionError)) as caught:
        _open_and_read(path)
    assert caught.value.format_unconfirmed is False


@requires("brotli")
@pytest.mark.parametrize("name", ["y.zip", "z.tar", "w.gz"])
def test_disagreeing_extension_does_not_corroborate(tmp_path: Path, name: str) -> None:
    """A name that disagrees must not corroborate — including across containers.

    ``z.tar`` is the case a bare ``stream``-only comparison got wrong: every container
    shares ``StreamFormat.UNCOMPRESSED``, so ``.tar``/``.zip`` would have "agreed" with any
    other container result. Pinned so ``ReadBackend.CONTENT_PROBES`` gaining a container
    probe cannot silently suppress the stamp.
    """
    path = tmp_path / name
    path.write_bytes(_probable_brotli_probe_only_residual())
    info = detect_format(path)
    assert info.format == ArchiveFormat.BROTLI
    assert info.detected_by == "content_probe"
    assert info.corroborated is False

    with pytest.raises((TruncatedError, CorruptionError)) as caught:
        _open_and_read(path)
    assert caught.value.format_unconfirmed is True


def test_extension_corroborates_rejects_cross_container_stream_match() -> None:
    """Unit pin on the predicate itself, for pairs no probe can produce today."""
    assert (
        _extension_corroborates((ArchiveFormat.ZIP, "zip"), ArchiveFormat.TAR) is False
    )
    assert (
        _extension_corroborates((ArchiveFormat.TAR, "tar"), ArchiveFormat.ZIP) is False
    )
    assert (
        _extension_corroborates((ArchiveFormat.RAR, "rar"), ArchiveFormat.ISO) is False
    )
    # Still true for what it is for.
    assert (
        _extension_corroborates((ArchiveFormat.BROTLI, "br"), ArchiveFormat.BROTLI)
        is True
    )
    assert _extension_corroborates((TAR_BROTLI, "tar.br"), ArchiveFormat.BROTLI) is True


@requires("brotli")
def test_inner_tar_upgrade_is_corroborated_and_probable() -> None:
    import brotli

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("a.txt")
        payload = b"hi"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    full = brotli.compress(buf.getvalue())
    info = detect_format(io.BytesIO(full))
    assert info.format == TAR_BROTLI
    assert info.confidence == DetectionConfidence.PROBABLE
    assert info.detected_by == "content_probe"
    assert info.corroborated is True

    with open_archive(io.BytesIO(full)) as reader:
        members = list(reader)
        assert members
        assert reader.open(members[0]).read() == b"hi"
        assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED not in {
            d.code for d in reader.diagnostics.retained
        }


@requires("brotli")
def test_inner_tar_decode_failure_does_not_stamp() -> None:
    import brotli

    # Truncate the *tar* after the ustar header region, then Brotli-compress: detection
    # still sees the inner TAR (corroboration), but listing/reading fails.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("a.txt")
        payload = b"x" * 50_000
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    truncated_tar = buf.getvalue()[:1024]
    assert truncated_tar[257:262] == b"ustar"
    blob = brotli.compress(truncated_tar)
    info = detect_format(io.BytesIO(blob))
    assert info.format == TAR_BROTLI
    assert info.corroborated is True
    with open_archive(io.BytesIO(blob)) as reader:
        with pytest.raises((TruncatedError, CorruptionError)) as caught:
            list(reader)
        assert caught.value.format_unconfirmed is False
        assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED not in {
            d.code for d in reader.diagnostics.retained
        }


@requires("brotli")
def test_probe_only_clean_read_stays_success() -> None:
    import brotli

    data = brotli.compress(b"payload " * 40)
    info = detect_format(io.BytesIO(data))
    assert info.format == ArchiveFormat.BROTLI
    assert info.detected_by == "content_probe"
    assert info.corroborated is False
    with open_archive(io.BytesIO(data)) as reader:
        assert reader.open(next(iter(reader))).read() == b"payload " * 40
        assert not reader.diagnostics.retained


@requires("brotli")
def test_pedantic_probable_probe_keeps_typed_error() -> None:
    blob = _probable_brotli_probe_only_residual()
    cfg = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.pedantic())
    diagnostics: list[Diagnostic] = []
    with pytest.raises((TruncatedError, CorruptionError)) as caught:
        _open_and_read(io.BytesIO(blob), diagnostics, config=cfg)
    assert not isinstance(caught.value, DiagnosticRaisedError)
    assert caught.value.format_unconfirmed is True
    assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED in {d.code for d in diagnostics}


@requires("brotli")
def test_confidence_matrix_unchanged_by_provenance() -> None:
    """Pin GUESS/PROBABLE so this change is not mistaken for a confidence retune."""
    import brotli

    guess = detect_format(io.BytesIO(_chain_surviving_guess_residual()))
    assert guess.confidence == DetectionConfidence.GUESS

    compressed = brotli.compress(b"payload " * 40)
    assert parse_metablock(compressed).outcome is BrotliBlock.COMPRESSED
    probable = detect_format(io.BytesIO(compressed))
    assert probable.confidence == DetectionConfidence.PROBABLE

    residual = detect_format(io.BytesIO(_probable_brotli_probe_only_residual()))
    assert residual.confidence == DetectionConfidence.PROBABLE


def test_exact_magic_failure_untouched_by_probe_channel(tmp_path: Path) -> None:
    # Truncated gzip (exact magic) must not grow a probe-unconfirmed stamp.
    import gzip

    path = tmp_path / "x.gz"
    path.write_bytes(gzip.compress(b"hello world")[:8])
    info = detect_format(path)
    assert info.detected_by == "magic"
    assert info.corroborated is False
    diagnostics: list[Diagnostic] = []
    with pytest.raises((TruncatedError, CorruptionError)) as caught:
        _open_and_read(path, diagnostics)
    assert caught.value.format_unconfirmed is False
    assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED not in {d.code for d in diagnostics}


_RAR_FIXTURES = Path(__file__).parent / "fixtures" / "rar"


def test_argument_provenance_records_the_resolved_volume() -> None:
    """``format=`` with a later RAR part records the first volume that was opened.

    The empty-listing check re-detects ``provenance.source`` by name, so it must be the
    file the reader read, not the name the caller passed.
    """
    later_part = _RAR_FIXTURES / "tinyvol_rnn.r00"
    with open_archive(later_part, format=ArchiveFormat.RAR) as archive:
        provenance = archive._format_provenance  # type: ignore[attr-defined]
        assert provenance is not None
        assert provenance.chosen_by == "argument"
        assert provenance.source == _RAR_FIXTURES / "tinyvol_rnn.rar"


def test_argument_provenance_of_a_stream_has_no_source() -> None:
    """A regression guard, not a red-green repro: ``main`` already passed this.

    It fails if the provenance falls back to the caller's argument for a source that
    has no single path, which would make the empty-listing check reach into a stream.
    """
    blob = (_RAR_FIXTURES / "stored_m0.rar").read_bytes()
    with open_archive(io.BytesIO(blob), format=ArchiveFormat.RAR) as archive:
        provenance = archive._format_provenance  # type: ignore[attr-defined]
        assert provenance is not None
        assert provenance.chosen_by == "argument"
        assert provenance.source is None


def test_empty_directory_with_format_directory_is_not_unconfirmed(
    tmp_path: Path,
) -> None:
    """``format=DIRECTORY`` on a directory is confirmed by the filesystem, not asserted."""
    with open_archive(tmp_path, format=ArchiveFormat.DIRECTORY) as archive:
        assert archive.members() == []
        codes = [d.code for d in archive.diagnostics.retained]
    assert codes == [DiagnosticCode.EMPTY_ARCHIVE]
