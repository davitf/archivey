"""A sparse file's logical bytes over its stored bytes, holes as zeros.

A sparse file is stored as its data chunks only, one after another, plus a map that
says where each chunk sits in the logical file. :class:`SparseStream` serves the
logical file: a read inside a chunk reads the stored bytes, a read inside a hole
returns zeros without touching the inner stream.

The map must already be validated by the caller: non-negative, in order, not
overlapping, inside ``size``, and summing to the stored bytes. For TAR,
``validate_sparse_map`` in the native TAR parser checks exactly these. This module
does not check them again, because what an invalid map means (damage, or a valid map
it cannot serve) is the format's decision, not this stream's.

The zeros of a hole are output the archive chooses, and this stream neither counts nor
caps them: the output limits (``max_extracted_bytes``, ``max_ratio``) must count above
it, where the logical bytes are.

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
    resolve_seek,
)
from archivey.internal.streams.streamtools.solid import skip_forward


class SparseStream(DelegatingStream):
    """The logical bytes of a sparse file of ``size`` bytes.

    ``offsets[i]`` is where chunk ``i`` starts in the logical file and ``lengths[i]``
    how long it is; chunk ``i`` is stored right after chunk ``i - 1`` in ``inner``.
    The first chunk starts where ``inner`` is positioned when this stream is made.
    Owns ``inner``, as every :class:`DelegatingStream` does.
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
        # A read of a zero-length chunk returns no bytes, so the read loop would
        # not advance.
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
        # The chunk that contains _pos, or the next chunk when _pos is in a hole.
        # len(self._starts) when _pos is past the last chunk. seek() sets this
        # with a search. read() increases it by one at a chunk's end.
        self._index = self._index_at(0)
        # Where the first chunk starts in the inner stream, and how far past it the
        # inner stream is now.
        self._base = inner.tell() if self._seekable else 0
        self._inner_pos = 0

    def _raise_if_closed(self) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed file.")

    def tell(self) -> int:
        self._raise_if_closed()
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        self._raise_if_closed()
        target = resolve_seek(offset, whence, pos=self._pos, end=lambda: self._size)
        if not self._seekable and target < self._pos:
            raise io.UnsupportedOperation(
                "backward seek on a forward-only sparse stream"
            )
        self._pos = target
        self._index = self._index_at(target)
        return target

    def _index_at(self, pos: int) -> int:
        # Ends increase: the map is ordered and non-overlapping, and a zero-length
        # chunk is dropped. The first end strictly after pos is the chunk that
        # contains pos, or the next chunk when pos is in a hole. An end equal to
        # pos belongs to a chunk the position has already left.
        return bisect_right(self._ends, pos)

    def read(self, n: int = -1, /) -> bytes:
        self._raise_if_closed()
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
        return b"".join(parts)

    def _read_some(self, want: int) -> bytes:
        """Up to ``want`` bytes from one chunk or one hole, advancing the position."""
        i = self._index
        if i == len(self._starts):
            return self._read_hole(want, self._size)
        start = self._starts[i]
        if self._pos < start:
            return self._read_hole(want, start)
        end = self._ends[i]
        pos = self._pos
        count = min(want, end - pos)
        data = self._read_stored(self._stored[i] + pos - start, count)
        self._pos = pos + count
        if self._pos == end:
            self._index = i + 1
        return data

    def _read_hole(self, want: int, boundary: int) -> bytes:
        count = min(want, boundary - self._pos)
        self._pos += count
        # Built whole: ``read`` already bounds ``want``, and a read that is one hole
        # returns this object without a join copying it.
        return bytes(count)

    def _read_stored(self, at: int, count: int) -> bytes:
        inner = self._inner
        if at != self._inner_pos:
            if self._seekable:
                inner.seek(self._base + at)
                self._inner_pos = at
            else:
                # Chunks are in logical order, so a forward-only inner only ever skips
                # forward: the reader moved past a chunk's start by a seek. Counted as
                # it goes, so a skip that raises leaves the position true.
                skip_forward(inner, at - self._inner_pos, on_chunk=self._advance_inner)
        # A full-count inner (ADR 0014): a short read is its end.
        data = inner.read(count)
        self._inner_pos += len(data)
        if len(data) < count:
            raise EOFError(f"sparse data ended {count - len(data)} bytes into a chunk")
        return data

    def _advance_inner(self, count: int) -> None:
        self._inner_pos += count
