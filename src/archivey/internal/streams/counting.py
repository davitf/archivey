"""Pass-through stream wrappers that count bytes or seeks.

Used for:

- *Compressed input pressure* (:class:`CountingReader`) — how many compressed bytes a
  decompressor has pulled from a source whose total size is not knowable (live
  decompression-ratio guard in ``safe-extraction``).
- *Decompressed output volume* (:class:`OutputCountingStream`) — bytes leaving a
  decode stage, for the benchmark solid-block invariant.
- *Source seek storms* (:class:`SeekCountingStream`) — ``seek`` calls on the
  archive-facing handle, for the benchmark seek axis.

Measurement wrappers share a :class:`~archivey.internal.measurement.ByteCounter` /
:class:`~archivey.internal.measurement.SeekCounter` so multiple layers can feed one
archive-level total. They are installed only when measurement is enabled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, BinaryIO

from archivey.internal.streams.resume import ask_resume_offset
from archivey.internal.streams.streamtools import DelegatingStream

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

    from archivey.internal.measurement import ByteCounter, SeekCounter


class CountingReader(DelegatingStream):
    """Wrap ``inner`` and count the total number of bytes returned by reads.

    Both read paths keep the count current: ``read`` counts what it returns and ``readinto``
    counts what it fills, so the zero-copy ``readinto`` fast path stays fast (no intermediate
    ``bytes`` object) instead of being routed through ``read``. The count reflects bytes
    *pulled from the source*; with a decompressor reading fixed-size chunks it overshoots the
    strictly-decoded amount by at most one read, which is negligible against the output of a
    real decompression bomb.
    """

    def __init__(self, inner: BinaryIO) -> None:
        super().__init__(inner)
        self._bytes_read = 0

    @property
    def bytes_read(self) -> int:
        return self._bytes_read

    def read(self, n: int = -1, /) -> bytes:
        data = self._inner.read(n)
        self._bytes_read += len(data)
        return data

    def readinto(self, b: "WriteableBuffer", /) -> int:
        inner_readinto = getattr(self._inner, "readinto", None)
        if inner_readinto is None:
            # No inner readinto: DelegatingStream routes through self.read(), which already
            # counts — so return its result without double-counting here.
            return super().readinto(b)
        n = inner_readinto(b)
        self._bytes_read += n
        return n


class OutputCountingStream(DelegatingStream):
    """Wrap a decoded (or stored) stream and add every delivered byte to ``counter``.

    Used for the benchmark ``bytes_decompressed`` axis. Unlike :class:`CountingReader`,
    this feeds a shared :class:`~archivey.internal.measurement.ByteCounter` so folder-level
    and member-level wrappers can share one archive total when needed.
    """

    def __init__(self, inner: BinaryIO, counter: "ByteCounter") -> None:
        super().__init__(inner)
        self._counter = counter

    def read(self, n: int = -1, /) -> bytes:
        data = self._inner.read(n)
        self._counter.add(len(data))
        return data

    def readinto(self, b: "WriteableBuffer", /) -> int:
        inner_readinto = getattr(self._inner, "readinto", None)
        if inner_readinto is None:
            return super().readinto(b)
        n = inner_readinto(b)
        self._counter.add(n)
        return n

    def nearest_resume_offset(self, target: int) -> int | None:
        # Measurement wrapper around decoded output; preserve the inner's offset space.
        return ask_resume_offset(self._inner, target)


class SeekCountingStream(DelegatingStream):
    """Wrap a source stream and count every ``seek`` call on ``counter``.

    ``read`` / ``readinto`` are pass-through (no byte counting). Installed only when
    measurement is on so the non-measure path pays nothing.

    ``peel_for_source_size`` is True: this wrapper sits on archive-facing handles
    and does not change their cheap size, so ``source_byte_size`` looks through it
    the same way with measurement on or off. Do not copy this onto
    :class:`OutputCountingStream` — that one sits on decompressors.
    """

    peel_for_source_size = True

    def __init__(self, inner: BinaryIO, counter: "SeekCounter") -> None:
        super().__init__(inner)
        self._counter = counter

    def seek(self, offset: int, whence: int = 0, /) -> int:
        self._counter.record()
        return self._inner.seek(offset, whence)
