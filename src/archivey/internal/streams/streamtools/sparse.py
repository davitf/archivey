"""A sparse file's logical bytes over its stored bytes, holes as zeros.

A sparse file is stored as its data chunks only, one after another, plus a map that
says where each chunk sits in the logical file. :class:`SparseStream` serves the
logical file: a read inside a chunk reads the stored bytes, a read inside a hole
returns zeros without touching the inner stream.

The map must already be validated by the caller: non-negative, in order, not
overlapping, inside ``size``, and summing to the inner stream's length. This module
does not check it again, because what an invalid map means (damage, or a valid map it
cannot serve) is the format's decision, not this stream's.

Seeking follows ``io.BytesIO``: any non-negative target is accepted, a read past the
end returns ``b""``. Over a forward-only inner the stream is forward-only too: reading
never needs to go back in the inner, because chunks are in logical order, but a
backward seek would.
"""

from __future__ import annotations

import io
from bisect import bisect_right
from collections.abc import Sequence
from typing import BinaryIO

from archivey.internal.streams.streamtools.base import DelegatingStream
from archivey.internal.streams.streamtools.binaryio import (
    check_read_size,
    is_seekable,
    resolve_seek,
)
from archivey.internal.streams.streamtools.solid import skip_forward

# The largest run of zeros one read builds at a time. A hole can be as large as the
# logical size, which the archive chooses.
_ZERO_STEP = 1 << 20


class SparseStream(DelegatingStream):
    """The logical bytes of a sparse file of ``size`` bytes.

    ``offsets[i]`` is where chunk ``i`` starts in the logical file and ``lengths[i]``
    how long it is; chunk ``i`` is stored right after chunk ``i - 1`` in ``inner``,
    which starts at the first chunk. Owns ``inner``, as every
    :class:`DelegatingStream` does.
    """

    # read() transforms the inner's bytes, so readinto must go through it.
    readinto_passthrough = False

    def __init__(
        self,
        inner: BinaryIO,
        offsets: Sequence[int],
        lengths: Sequence[int],
        size: int,
    ) -> None:
        super().__init__(inner)
        # Empty chunks hold no bytes and would only make the search ambiguous.
        chunks = [(o, n) for o, n in zip(offsets, lengths, strict=True) if n]
        self._starts = [o for o, _ in chunks]
        self._ends = [o + n for o, n in chunks]
        # Where each chunk starts in the inner stream.
        self._stored: list[int] = []
        total = 0
        for _, n in chunks:
            self._stored.append(total)
            total += n
        self._size = size
        self._pos = 0
        # Where the inner stream is now, in its own offsets.
        self._inner_pos = 0
        self._seekable_inner = is_seekable(inner)

    def seekable(self) -> bool:
        return self._seekable_inner

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        target = resolve_seek(offset, whence, pos=self._pos, end=lambda: self._size)
        if not self._seekable_inner and target < self._pos:
            raise io.UnsupportedOperation(
                "backward seek on a forward-only sparse stream"
            )
        self._pos = target
        return target

    def read(self, n: int = -1, /) -> bytes:
        if self.closed:
            raise ValueError("read from a closed sparse stream")
        n = check_read_size(n)
        left = self._size - self._pos
        if left <= 0:
            return b""
        want = left if n < 0 else min(n, left)
        parts: list[bytes] = []
        while want:
            part = self._read_some(want)
            parts.append(part)
            want -= len(part)
            self._pos += len(part)
        return b"".join(parts)

    def _read_some(self, want: int) -> bytes:
        """Up to ``want`` bytes from ``self._pos``, from one chunk or one hole."""
        i = bisect_right(self._starts, self._pos) - 1
        if i >= 0 and self._pos < self._ends[i]:
            count = min(want, self._ends[i] - self._pos)
            return self._read_stored(
                self._stored[i] + self._pos - self._starts[i], count
            )
        next_start = self._starts[i + 1] if i + 1 < len(self._starts) else self._size
        return bytes(min(want, next_start - self._pos, _ZERO_STEP))

    def _read_stored(self, at: int, count: int) -> bytes:
        inner = self._inner
        if at != self._inner_pos:
            if self._seekable_inner:
                inner.seek(at)
            else:
                # Chunks are in logical order, so a forward-only inner only ever skips
                # forward: the reader moved past a chunk's start by a seek.
                skip_forward(inner, at - self._inner_pos)
            self._inner_pos = at
        # A full-count inner (ADR 0014): a short read is its end.
        data = inner.read(count)
        self._inner_pos += len(data)
        if len(data) < count:
            raise EOFError(f"sparse data ended {count - len(data)} bytes into a chunk")
        return data
