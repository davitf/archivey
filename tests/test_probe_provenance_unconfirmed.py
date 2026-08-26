"""Provenance-based ``format_unconfirmed`` (probe-provenance-unconfirmed).

The honesty channel stamps a decode failure when a content probe was the *sole*
evidence — at any ``DetectionConfidence`` — and leaves corroborated hits alone.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from archivey import (
    ArchiveFormat,
    ArchiveyConfig,
    DetectionConfidence,
    DiagnosticPolicy,
    detect_format,
    open_archive,
)
from archivey.diagnostics import DiagnosticCode
from archivey.exceptions import (
    CorruptionError,
    DiagnosticRaisedError,
    TruncatedError,
)
from archivey.internal.detection import _extension_corroborates
from archivey.internal.streams.brotli_framing import BrotliBlock, parse_metablock
from archivey.internal.streams.peekable import DETECTION_LIMIT
from archivey.types import ContainerFormat, StreamFormat
from tests.conftest import requires

TAR_BROTLI = ArchiveFormat(ContainerFormat.TAR, StreamFormat.BROTLI)


def _probable_brotli_probe_only_residual() -> bytes:
    """Compressed-first ASCII residual above ``DETECTION_LIMIT``, no extension cue.

    Completeness rejects sources no larger than the peeked prefix, so the fixture must
    sit above that line. Ordinary Perl-module text of a few KiB is the measured class;
    a short distinctive unit repeated past the limit is enough to keep the probe at
    ``BROTLI`` / ``PROBABLE`` / ``content_probe``.
    """
    unit = (
        b"package TAP::Parser::SourceHandler;\n\n"
        b"use strict;\nuse warnings;\n\n"
        b"use TAP::Parser::Iterator;\n"
    )
    target = DETECTION_LIMIT + 104
    blob = (unit * ((target // len(unit)) + 1))[:target]
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

    with open_archive(io.BytesIO(blob)) as reader:
        with pytest.raises((TruncatedError, CorruptionError)) as caught:
            reader.open(next(iter(reader))).read()
        exc = caught.value
        assert exc.format_unconfirmed is True
        assert "unconfirmed" in exc.message.lower()
        assert "Partial output may already have been produced" in exc.raw_message
        assert "GUESS" not in exc.raw_message
        messages = " ".join(d.message for d in reader.diagnostics.retained)
        assert "GUESS" not in messages
        assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED in {
            d.code for d in reader.diagnostics.retained
        }


def test_lzma_alone_probable_failure_sets_format_unconfirmed() -> None:
    blob = _ole_lzma_alone_residual()
    info = detect_format(io.BytesIO(blob))
    assert info.format == ArchiveFormat.LZMA_ALONE
    assert info.confidence == DetectionConfidence.PROBABLE
    assert info.detected_by == "content_probe"
    assert info.corroborated is False

    with open_archive(io.BytesIO(blob)) as reader:
        with pytest.raises((TruncatedError, CorruptionError)) as caught:
            reader.open(next(iter(reader))).read()
        assert caught.value.format_unconfirmed is True
        codes = {d.code for d in reader.diagnostics.retained}
        assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED in codes
        assert DiagnosticCode.EXTENSION_FORMAT_UNCONFIRMED not in codes


@requires("brotli")
def test_br_extension_failure_does_not_stamp(tmp_path: Path) -> None:
    # Same residual as the probe-only case, but ``.br`` corroborates the claim.
    path = tmp_path / "x.br"
    path.write_bytes(_probable_brotli_probe_only_residual())
    info = detect_format(path)
    assert info.format == ArchiveFormat.BROTLI
    assert info.detected_by == "content_probe"
    assert info.corroborated is True
    with open_archive(path) as reader:
        with pytest.raises((TruncatedError, CorruptionError)) as caught:
            reader.open(next(iter(reader))).read()
        assert caught.value.format_unconfirmed is False
        assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED not in {
            d.code for d in reader.diagnostics.retained
        }


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

    with open_archive(path) as reader:
        with pytest.raises((TruncatedError, CorruptionError)) as caught:
            reader.open(next(iter(reader))).read()
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

    with open_archive(path) as reader:
        with pytest.raises((TruncatedError, CorruptionError)) as caught:
            reader.open(next(iter(reader))).read()
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
    with open_archive(io.BytesIO(blob), config=cfg) as reader:
        with pytest.raises((TruncatedError, CorruptionError)) as caught:
            reader.open(next(iter(reader))).read()
        assert not isinstance(caught.value, DiagnosticRaisedError)
        assert caught.value.format_unconfirmed is True
        assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED in {
            d.code for d in reader.diagnostics.retained
        }


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
    with open_archive(path) as reader:
        with pytest.raises((TruncatedError, CorruptionError)) as caught:
            reader.open(next(iter(reader))).read()
        assert caught.value.format_unconfirmed is False
        assert DiagnosticCode.PROBE_FORMAT_UNCONFIRMED not in {
            d.code for d in reader.diagnostics.retained
        }
