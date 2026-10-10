"""The Deflate64 decoder (inflate64)."""

from __future__ import annotations

import os
from typing import BinaryIO, Protocol

from archivey.exceptions import TruncatedError
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.streams.decompressor_stream import (
    BaseDecoder,
    DecodeOut,
    DecompressorStream,
    SeekPoint,
)


class _Inflate64Inflater(Protocol):
    """The ``inflate64.Inflater`` methods this adapter calls.

    Declared here because ``inflate64`` is an optional extra with no stubs.
    """

    def inflate(self, data: bytes) -> bytes: ...

    @property
    def eof(self) -> bool: ...


class Deflate64Decoder(BaseDecoder):
    """Decode a Deflate64 stream via ``inflate64.Inflater``.

    ``inflate64`` has no output-size parameter: one ``inflate`` of a small
    highly-compressible feed can still allocate the full expansion. When the
    stream passes ``max_length >= 0``, feed compressed input in small steps
    (see ``_BUDGETED_FEED``) and retain any overshoot in ``_pending_out`` so
    ``read(n)`` peak buffers stay near the caller's budget.

    Feed-size tradeoff on a 100 MiB zeros Deflate64 bomb (per-call max_out /
    throughput): 1→514 B / ~320 MiB/s; 64→19 KiB / ~700 MiB/s; 256→70 KiB /
    ~710 MiB/s; 64 KiB→18 MiB / ~460 MiB/s. 64 keeps peaks under a 64 KiB
    read budget while recovering most of the speed of larger feeds.
    """

    # Compressed bytes per inflate() under a max_length budget. See class docstring.
    _BUDGETED_FEED = 64

    def __init__(self) -> None:
        import inflate64

        self._decomp: _Inflate64Inflater = inflate64.Inflater()
        self._pending = b""
        self._pending_out = b""

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Deflate64Decoder:
        del point, inner
        return Deflate64Decoder()

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        data = self._pending + chunk
        self._pending = b""
        if max_length < 0:
            if self._pending_out:
                data = self._pending_out + (self._decomp.inflate(data) if data else b"")
                self._pending_out = b""
                return DecodeOut(data)
            if not data:
                return DecodeOut(b"")
            return DecodeOut(self._decomp.inflate(data))

        out = bytearray()
        if self._pending_out:
            take = min(len(self._pending_out), max_length)
            out += self._pending_out[:take]
            self._pending_out = self._pending_out[take:]
            if len(out) >= max_length:
                self._pending = data
                return DecodeOut(bytes(out))

        step = self._BUDGETED_FEED
        while data and len(out) < max_length:
            produced = self._decomp.inflate(data[:step])
            data = data[step:]
            room = max_length - len(out)
            if len(produced) > room:
                out += produced[:room]
                self._pending_out = produced[room:]
                break
            out += produced
        self._pending = data
        return DecodeOut(bytes(out))

    def flush(self) -> DecodeOut:
        # Flush remaining state with an empty feed (mirrors py7zr's Deflate64Decompressor).
        if self._pending_out:
            out = self._pending_out
            self._pending_out = b""
            if not self._decomp.eof:
                out += self._decomp.inflate(b"")
        elif self._decomp.eof:
            out = b""
        else:
            out = self._decomp.inflate(b"")
        if not self.finished:
            self._pending_error = TruncatedError("File is truncated")
        return DecodeOut(out)

    @property
    def finished(self) -> bool:
        return bool(self._decomp.eof) and not self._pending_out

    @property
    def needs_input(self) -> bool:
        return not self._pending and not self._pending_out


def Deflate64DecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
    *,
    collector: DiagnosticCollector | None = None,
    report_trailing_data: bool = False,
) -> DecompressorStream:
    """Decode a Deflate64 stream (forward-only)."""
    return DecompressorStream(
        path,
        make_decoder=lambda _p, _i: Deflate64Decoder(),
        collector=collector,
        codec_name="deflate64",
        report_trailing_data=report_trailing_data,
    )
