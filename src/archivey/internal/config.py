"""Internal stream-layer view derived from :class:`ArchiveyConfig`."""

from __future__ import annotations

from dataclasses import dataclass

from archivey.config import (
    DEFAULT_ARCHIVEY_CONFIG,
    AcceleratorMode,
    ArchiveyConfig,
    DecoderLimits,
)

# Only the names other modules import from here. ``ArchiveyConfig`` and
# ``DEFAULT_ARCHIVEY_CONFIG`` are imported for use below, not re-exported —
# callers take those from ``archivey.config``.
__all__ = [
    "AcceleratorMode",
    "DEFAULT_STREAM_CONFIG",
    "DecoderLimits",
    "StreamConfig",
    "stream_config_from_archivey",
]


@dataclass(frozen=True)
class StreamConfig:
    """Options that influence how compressed streams are opened.

    ``seekable`` is declared seek demand (``MemberStreams.SEEKABLE``): accelerator
    ``AUTO`` resolution and index construction key off it. ``streaming`` remains the
    archive access mode for backends that still need to know forward-only vs random.
    ``compressed_input_size`` is the known compressed byte length of the source (path
    size, slice length, …), used by ``use_rapidgzip`` AUTO's minimum-size gate; ``None``
    means unknown (AUTO keeps pre-threshold behaviour when truncation is also
    verifiable). ``expected_decompressed_size`` is a container-declared uncompressed
    length (ZIP central-dir size, …) used both to gate rapidgzip AUTO and to wrap the
    accelerator in a length-verifying stream. ``gzip_isize_backstop`` is set when a
    seekable gzip source has a readable ISIZE trailer — enough for AUTO to select
    rapidgzip with the ISIZE truncation check, but *not* a hard ``VerifyingStream``
    bound (ISIZE is mod 2**32 and multi-member trailers only cover the last member).
    ``decoder_limits`` is the caller's :class:`~archivey.config.DecoderLimits`,
    carried down so a codec can refuse an archive-declared allocation before making
    it; it defaults to the public default rather than to "unlimited", because a
    :class:`StreamConfig` built directly (detection, tests) is still decoding a file
    someone else wrote.
    """

    streaming: bool = False
    seekable: bool = False
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    compressed_input_size: int | None = None
    expected_decompressed_size: int | None = None
    gzip_isize_backstop: bool = False
    decoder_limits: DecoderLimits = DecoderLimits()


def stream_config_from_archivey(
    config: ArchiveyConfig,
    *,
    streaming: bool,
    seekable: bool = False,
) -> StreamConfig:
    """Derive the codec-layer view from the public config and declared seek demand."""
    return StreamConfig(
        streaming=streaming,
        seekable=seekable,
        use_rapidgzip=config.use_rapidgzip,
        use_indexed_bzip2=config.use_indexed_bzip2,
        decoder_limits=config.decoder_limits,
    )


DEFAULT_STREAM_CONFIG = stream_config_from_archivey(
    DEFAULT_ARCHIVEY_CONFIG, streaming=False, seekable=False
)
