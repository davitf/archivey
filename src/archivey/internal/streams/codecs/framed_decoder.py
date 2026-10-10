"""A decoder for one-shot decompressors that end at a stream end marker (bzip2,
zstd, LZ4): the stream-start magic that tells a following stream from trailing data.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import BinaryIO, Protocol

from archivey.exceptions import TruncatedError
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.streams.codecs.bzip2_resume import (
    Bzip2Resume,
    Bzip2ResumeDecoder,
)
from archivey.internal.streams.decompressor_stream import (
    BaseDecoder,
    DecodeOut,
    Decoder,
    DecompressorStream,
    SeekPoint,
)


class _OneStreamDecompressor(Protocol):
    """The one-stream decompressor objects of ``bz2``, ``lzma``, ``zstd`` and ``lz4.frame``."""

    def decompress(self, data: bytes, max_length: int = ...) -> bytes: ...

    @property
    def eof(self) -> bool: ...

    @property
    def unused_data(self) -> bytes | None: ...

    @property
    def needs_input(self) -> bool: ...


# A stream's magic, as the bytes each position may hold; a codec lists one per kind of
# stream that may follow the first (zstd: a frame or a skippable frame).
StreamMagic = tuple[tuple[frozenset[int], ...], ...]
# Given the bytes after a stream: True when they start another, None when more bytes are
# needed to tell, False when they do not. It may also refuse the stream by raising, as
# the LZMA Alone check does for a dictionary over ``max_decoder_memory``, so its
# result must not be cached or its call skipped.
StreamStart = Callable[[bytes], bool | None]


def stream_magic(*alternatives: tuple[bytes | range, ...]) -> StreamStart:
    """A :data:`StreamStart` matching any of ``alternatives``, per-position bytes or ranges."""
    magic: StreamMagic = tuple(
        tuple(frozenset(position) for position in alternative)
        for alternative in alternatives
    )
    return functools.partial(_magic_state, magic=magic)


def _magic_state(data: bytes, magic: StreamMagic) -> bool | None:
    """True when ``data`` starts a stream, None when it may once more bytes come."""
    for alternative in magic:
        seen = min(len(data), len(alternative))
        if all(data[i] in alternative[i] for i in range(seen)):
            return True if seen == len(alternative) else None
    return False


class FramedDecoder(BaseDecoder):
    """Decode a codec whose library decompressor stops at the end of one stream.

    ``bz2``, ``lzma`` (Alone), ``zstd`` and ``lz4.frame`` each have a one-stream
    decompressor with ``decompress(data, max_length)``, ``eof``, ``unused_data`` and
    ``needs_input``. Their file readers decide on their own what may follow a stream,
    and disagree: ``bz2.open`` ignores anything that does not decode, zstd and lz4
    raise on it. This adapter decides it the same way for all of them. Bytes that start
    ``magic`` begin another stream (a concatenated file); zeros are padding; anything
    else ends the data and sets :attr:`trailing_bytes`. A codec with no magic (LZMA
    Alone) passes a :data:`StreamStart` check of the header instead, which may raise to
    refuse the next stream. ``zero_padding=False`` hands zeros to that check too (raw
    LZMA, where 7-Zip refuses any byte after the end marker).

    The first stream is handed to the library as it comes, so a file that is not this
    codec at all fails with the library's own error. An empty source, or one that ends
    inside a stream, is truncated. A stream with no end mark (legacy LZ4) ends with its
    input instead, when its decompressor's ``complete_at_end_of_input`` says the input
    stopped between blocks.
    """

    def __init__(
        self,
        new_decompressor: Callable[[], _OneStreamDecompressor],
        *,
        magic: StreamStart,
        zero_padding: bool = True,
    ) -> None:
        self._new = new_decompressor
        self._magic = magic
        self._zero_padding = zero_padding
        self._decomp = new_decompressor()
        self._fed = False
        # Past a stream's end, looking for the next one.
        self._between = False
        # Input not yet handed on: kept when an output budget ran out, or a prefix of
        # the next stream's magic waiting for its remaining bytes (``_need_more``).
        self._held = b""
        self._need_more = False
        self._done = False

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Decoder:
        del inner
        if isinstance(point.state, Bzip2Resume):
            # Only the bzip2 takeover adds such a point (``bzip2_resume``).
            return Bzip2ResumeDecoder(point.state, self)
        return FramedDecoder(
            self._new, magic=self._magic, zero_padding=self._zero_padding
        )

    def _next_stream(self, data: bytes) -> bytes:
        """Resolve ``data`` past a stream's end: the next stream's input, or ``b""``.

        A further stream continues the output only where ``magic`` accepts it; a
        container coder that is one stream (``CodecParams.single_stream``) accepts
        none, so a further stream there (a skippable zstd frame too) is input after
        the end, like any other byte that starts no stream.
        """
        rest = data.lstrip(b"\x00") if self._zero_padding else data
        if len(rest) < len(data):
            # Zero padding is input after a stream's end, which a
            # ``refuse_input_after_end`` stream refuses (``input_after_end``).
            self._input_after_end = True
        if not rest:
            return b""
        state = self._magic(rest)
        if state is None:
            self._held = rest
            self._need_more = True
            return b""
        if state:
            self._decomp = self._new()
            self._between = False
            return rest
        self._past_end(rest)
        self._done = True
        return b""

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        if self._done:
            return DecodeOut(b"")
        data = self._held + chunk
        self._held = b""
        self._need_more = False
        self._fed = self._fed or bool(data)
        output = bytearray()
        while True:
            if max_length >= 0 and len(output) >= max_length:
                self._held = data
                break
            if self._between:
                data = self._next_stream(data)
                if self._between or self._done:
                    break
            limit = max_length - len(output) if max_length >= 0 else -1
            produced = self._decomp.decompress(data, limit)
            output.extend(produced)
            data = b""
            if self._decomp.eof:
                # lz4 reports no leftover as None rather than b"".
                data = self._decomp.unused_data or b""
                self._between = True
                continue
            if self._decomp.needs_input or not produced:
                break
        return DecodeOut(bytes(output))

    def flush(self) -> DecodeOut:
        if self._done:
            return DecodeOut(b"")
        if self._between:
            # A prefix of another stream's magic is where the file ends: too short to
            # be a stream, so it is what follows this one.
            self._past_end(self._held)
            self._held = b""
            self._done = True
            return DecodeOut(b"")
        if getattr(self._decomp, "complete_at_end_of_input", False):
            # A stream with no end mark (legacy LZ4) ends where its input does.
            self._done = True
            return DecodeOut(b"")
        self._pending_error = TruncatedError(
            "File is truncated" if self._fed else "File is empty"
        )
        return DecodeOut(b"")

    @property
    def finished(self) -> bool:
        return self._done

    @property
    def needs_input(self) -> bool:
        if self._done:
            return True
        if self._held:
            return self._need_more
        return self._between or self._decomp.needs_input


def FramedDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
    new_decompressor: Callable[[], _OneStreamDecompressor],
    *,
    codec_name: str,
    magic: StreamStart,
    zero_padding: bool = True,
    collector: DiagnosticCollector | None = None,
    report_trailing_data: bool = False,
    refuse_input_after_end: bool = False,
) -> DecompressorStream:
    """Decode a one-stream library decompressor's codec (forward-only; O(n) rewind)."""
    return DecompressorStream(
        path,
        make_decoder=lambda _p, _i: FramedDecoder(
            new_decompressor, magic=magic, zero_padding=zero_padding
        ),
        collector=collector,
        codec_name=codec_name,
        report_trailing_data=report_trailing_data,
        refuse_input_after_end=refuse_input_after_end,
    )
