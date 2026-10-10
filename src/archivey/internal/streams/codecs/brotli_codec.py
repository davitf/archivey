"""Brotli."""

from __future__ import annotations

from typing import BinaryIO

from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
)
from archivey.internal.config import StreamConfig
from archivey.internal.streams.archive_stream import RewindWarning
from archivey.internal.streams.codecs import deps
from archivey.internal.streams.codecs.base import (
    Codec,
    CodecParams,
    CodecSource,
    ProbeReadAt,
    StreamCodec,
)
from archivey.internal.streams.codecs.brotli_decoder import BrotliDecompressorStream
from archivey.internal.streams.codecs.brotli_framing import (
    chain_proves_invalid,
    first_block_overruns_source,
)
from archivey.types import (
    MissingComponent,
    StreamFormat,
)


class BrotliCodec(StreamCodec):
    codec = Codec.BROTLI
    stream_format = StreamFormat.BROTLI
    # Brotli has no signature; the detector recognizes it by decoding a bounded prefix.
    requirement = MissingComponent(
        "brotli", "pip install archivey[recommended]", ("brotli",)
    )

    def _backend_present(self) -> bool:
        return deps.brotli.available()

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        if deps.brotli.load() is None:
            raise self._missing("Brotli streams")
        # Brotli has no random-access index; a backward seek re-decodes from the start (the
        # outer ArchiveStream warns — see rewind_warning).
        return BrotliDecompressorStream(
            source,
            collector=config.collector,
            report_trailing_data=config.report_trailing_data,
            exact_input=config.exact_input,
        )

    def translate(self, exc: Exception) -> ArchiveyError | None:
        # brotli raises its own brotli.error for corrupt data; a truncated stream doesn't
        # raise here (the decompressor just never reports finished), so the base
        # DecompressorStream surfaces that as TruncatedError on its own.
        brotli = deps.brotli.loaded()
        if brotli is not None and isinstance(exc, brotli.error):
            return CorruptionError(f"Error reading brotli stream: {exc!r}")
        return None

    def rewind_warning(self, config: StreamConfig) -> RewindWarning | None:
        return RewindWarning("brotli")

    def content_probe(
        self,
        prefix: bytes,
        *,
        source_length: int | None = None,
        read_at: ProbeReadAt | None = None,
    ) -> bool:
        """Recognize a raw Brotli stream — which has no magic — by decoding a bounded prefix.

        When ``source_length`` is known, reject a first meta-block that *declares* more
        bytes than the source can hold (uncompressed / metadata), then follow the
        self-describing block chain when a ``read_at`` facility (or the prefix alone)
        can reach successor offsets. Completeness — whole source visible and decode
        wants more input — is applied inside ``_decodes_sample``.
        """
        if source_length is not None:
            if first_block_overruns_source(prefix, source_length):
                return False
            if chain_proves_invalid(prefix, source_length, read_at=read_at):
                return False
        return self._decodes_sample(prefix, source_length=source_length)
