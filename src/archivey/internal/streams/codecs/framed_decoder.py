"""A decoder for one-shot decompressors that end at a stream end marker (bzip2,
zstd, LZ4): the stream-start magic that tells a following stream from trailing data.
"""

from __future__ import annotations

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
    damaged_stream_error,
    near_stream_magic,
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
# the LZMA Alone check does for a dictionary over ``max_decoder_memory`` and a magic
# check does for a damaged magic, so its result must not be cached or its call skipped.
StreamStart = Callable[[bytes], bool | None]


def stream_magic(*alternatives: tuple[bytes | range, ...]) -> StreamStart:
    """A :data:`StreamStart` matching any of ``alternatives``, per-position bytes or ranges."""
    return MagicStart(
        tuple(
            tuple(frozenset(position) for position in alternative)
            for alternative in alternatives
        )
    )


class MagicStart:
    """The :data:`StreamStart` that :func:`stream_magic` makes: a check of ``magic``.

    :class:`FramedDecoder` also asks it whether bytes that begin inside a short run of
    zeros are a damaged stream (:meth:`damaged`), since a damaged byte can be a zero.
    """

    def __init__(self, magic: StreamMagic) -> None:
        self.magic = magic
        # The longest alternative. A run of this many zeros is padding, not the start
        # of a damaged magic.
        self.width = max((len(alternative) for alternative in magic), default=0)

    def __call__(self, data: bytes) -> bool | None:
        return _magic_state(data, self.magic)

    def damaged(self, data: bytes) -> bool:
        """Whether ``data`` starts like a damaged stream (:func:`near_stream_magic`)."""
        return any(near_stream_magic(data, alternative) for alternative in self.magic)


def _magic_state(data: bytes, magic: StreamMagic) -> bool | None:
    """True when ``data`` starts a stream, False when it does not, None when the bytes
    so far cannot tell.

    ``None`` comes for a ``data`` that is a prefix of a magic, and also for any
    ``data`` shorter than an alternative that it does not start: such bytes cannot
    start that stream, but bytes that start like a damaged stream
    (:func:`near_stream_magic`) raise :class:`CorruptionError`, and that test needs a
    whole magic. So a short ``data`` is waited on; at the end of the source it is
    trailing data (``flush``).
    """
    for alternative in magic:
        seen = min(len(data), len(alternative))
        if all(data[i] in alternative[i] for i in range(seen)):
            return True if seen == len(alternative) else None
    undecided = False
    for alternative in magic:
        if len(data) < len(alternative):
            undecided = True
            continue
        if near_stream_magic(data, alternative):
            raise damaged_stream_error()
    return None if undecided else False


class FramedDecoder(BaseDecoder):
    """Decode a codec whose library decompressor stops at the end of one stream.

    ``bz2``, ``lzma`` (Alone), ``zstd`` and ``lz4.frame`` each have a one-stream
    decompressor with ``decompress(data, max_length)``, ``eof``, ``unused_data`` and
    ``needs_input``. Their file readers decide on their own what may follow a stream,
    and disagree: ``bz2.open`` ignores anything that does not decode, zstd and lz4
    raise on it. This adapter decides it the same way for all of them. Bytes that start
    ``magic`` right after a stream begin another stream (a concatenated file); bytes
    there that hold at least half of the magic, but not all, are a damaged stream and
    raise :class:`CorruptionError` (:func:`near_stream_magic`). Zeros that run to the
    end of the input are padding. Anything else ends the data and sets
    :attr:`trailing_bytes`. That includes any byte after zeros, a further stream's
    magic too: the first non-zero byte after them is where the trailing bytes start.
    One exception: a run of zeros shorter than the magic is judged with the bytes after
    it as the start of a damaged stream, since the damaged byte can be a zero.
    Each codec's own tool stops at zeros between streams too: ``bzip2``, ``zstd``,
    ``lz4`` and ``xz --format=lzma``, and 7-Zip for bzip2 and LZMA Alone. ``zstd``,
    ``lz4`` and ``xz --format=lzma`` also refuse zeros at the end of the file, which
    archivey accepts on purpose, as for every codec (``dev-docs/formats/single-file.md``
    §6). A codec with no magic (LZMA Alone) passes a :data:`StreamStart` check of the
    header instead, which may raise to refuse the next stream. ``zero_padding=False``
    hands zeros to that check too (raw LZMA, where 7-Zip refuses any byte after the end
    marker).

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
        # The magic-based codecs only (not LZMA Alone): they also judge a short run of
        # zeros as the start of a damaged magic.
        self._magic_start = magic if isinstance(magic, MagicStart) else None
        self._zero_padding = zero_padding
        self._decomp = new_decompressor()
        self._fed = False
        # Past a stream's end, looking for the next one.
        self._between = False
        # Past a stream's end, zeros have been skipped; no stream follows them.
        self._padded = False
        # Input not yet handed on: kept when an output budget ran out, or bytes after a
        # stream, fewer than a magic, waiting for the rest (``_need_more``).
        self._held = b""
        self._need_more = False
        self._done = False

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Decoder:
        del inner
        if isinstance(point.state, Bzip2Resume):
            # Only the bzip2 takeover adds such a point (``bzip2_resume``).
            return Bzip2ResumeDecoder(point.state, self)
        return FramedDecoder(
            self._new,
            magic=self._magic,
            zero_padding=self._zero_padding,
        )

    def _next_stream(self, data: bytes) -> bytes:
        """Resolve ``data`` past a stream's end: the next stream's input, or ``b""``."""
        if data:
            # Any byte after a stream's end, zero padding or another stream too
            # (a skippable zstd frame included), is input a container may refuse
            # (``input_after_end``).
            self._input_after_end = True
        rest = data.lstrip(b"\x00") if self._zero_padding else data
        self._padded = self._padded or len(rest) < len(data)
        width = self._magic_start.width if self._magic_start is not None else 0
        # A run of ``width`` zeros or more is padding whatever its length, so no more
        # than ``width`` of them are kept: the decision then does not depend on where
        # the source's chunks end.
        zeros = len(data) - len(rest)
        data = data[max(0, zeros - width) :]
        if not rest:
            self._held = data
            self._need_more = bool(data)
            return b""
        if self._padded:
            # After zeros no stream is read. Only a run shorter than the magic is still
            # judged, as a damaged magic's first bytes, which needs a magic's length.
            if 0 < zeros < width and len(data) < width:
                self._held = data
                self._need_more = True
                return b""
            self._judge_zero_run(data, zeros)
            self._past_end(rest)
            self._done = True
            return b""
        state = self._magic(rest)
        if state is None:
            self._held = data
            self._need_more = True
            return b""
        if state:
            self._decomp = self._new()
            self._between = False
            return rest
        self._past_end(rest)
        self._done = True
        return b""

    def _judge_zero_run(self, data: bytes, zeros: int) -> None:
        """Raise when ``data``, past a stream's end, starts with ``zeros`` zeros that
        begin a damaged stream.

        A damaged first byte can be a zero, which the padding strip takes for padding.
        So a run shorter than the magic is also judged as its start. ``_next_stream``
        and ``flush`` both ask, so the end of the input judges the bytes as the middle
        of the file does.
        """
        magic = self._magic_start
        if magic is not None and 0 < zeros < magic.width and magic.damaged(data):
            raise damaged_stream_error()

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
            # The file ends inside the bytes held after this stream. A short run of
            # zeros and the bytes after it can still be a magic wide, and a damaged
            # stream (as in ``_next_stream``). The width counts from the first zero:
            # ``\x00Zh9`` is a bzip2 magic wide and raises. Fewer bytes than that,
            # zeros included, are too short to tell, so they are what follows this
            # one. ``_held`` keeps at most ``width`` zeros (``_next_stream``), so the
            # capped count decides ``zeros < width`` exactly as the uncapped one does.
            held = self._held
            if self._zero_padding:
                self._judge_zero_run(held, len(held) - len(held.lstrip(b"\x00")))
            self._past_end(held)
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
            new_decompressor,
            magic=magic,
            zero_padding=zero_padding,
        ),
        collector=collector,
        codec_name=codec_name,
        report_trailing_data=report_trailing_data,
        refuse_input_after_end=refuse_input_after_end,
    )
