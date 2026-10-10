"""Deflate64 (ZIP method 9)."""

from __future__ import annotations

import zlib
from typing import BinaryIO

from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    TruncatedError,
)
from archivey.internal.config import StreamConfig
from archivey.internal.streams.codecs import deps
from archivey.internal.streams.codecs.base import (
    Codec,
    CodecParams,
    CodecSource,
    StreamCodec,
)
from archivey.internal.streams.codecs.deflate64_decoder import (
    Deflate64DecompressorStream,
)
from archivey.types import MissingComponent


class Deflate64Codec(StreamCodec):
    codec = Codec.DEFLATE64
    requirement = MissingComponent(
        "inflate64", "pip install archivey[recommended]", ("deflate64",)
    )

    def _backend_present(self) -> bool:
        return deps.inflate64.available()

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        if deps.inflate64.load() is None:
            raise self._missing("Deflate64 streams")
        return Deflate64DecompressorStream(source, exact_input=config.exact_input)

    def translate(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, EOFError):
            return TruncatedError(f"deflate64 stream is truncated: {exc!r}")
        if isinstance(exc, (ValueError, zlib.error)):
            return CorruptionError(f"Error reading deflate64 stream: {exc!r}")
        return None
