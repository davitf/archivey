"""Demultiplex one forward-only decoded stream into consecutive member sub-streams.

A *solid block* — a 7z folder, or a RAR ``unrar p`` pipe — decodes to a single
forward-only (often non-seekable) byte stream whose members occupy consecutive,
known-size ranges. :class:`SolidBlockReader` owns that stream and hands out one member
sub-stream at a time, skipping forward **lazily**: the gap before a member is consumed
only when the *next* member is opened, so closing a never-advanced member costs nothing.
Closing the reader closes the block without draining.

Not the same as :class:`~archivey.internal.streams.streamtools.shared.SharedSource`
(independent seekable views) or :class:`~archivey.internal.streams.streamtools.locked.LockedStream`
(lock around seek+read on one handle). This class is deliberately forward-only.

``open_member(..., lazy=True)`` defers open/skip until the first read — so an
iterator that yields then closes an unselected member pays nothing.

Like the rest of ``streamtools``, truncated blocks surface as plain :class:`EOFError`
for the caller to translate.

The closed / superseded / same-offset guards on :class:`_MemberSlice` are
defence-in-depth for this primitive's own contract. Current backends serialize
member access via ``_drive_pass_streams(close_previous=True)``, so no public
``open_archive`` path holds two live slices at once.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import BinaryIO

from archivey.internal.streams.streamtools.base import ReadOnlyIOStream

_SKIP_CHUNK = 1 << 20  # 1 MiB


def _drain_chunks(stream: BinaryIO, count: int) -> Iterator[int]:
    """Yield the length of each chunk read while discarding up to ``count`` bytes."""
    remaining = count
    while remaining > 0:
        chunk = stream.read(min(remaining, _SKIP_CHUNK))
        if not chunk:
            return
        yield len(chunk)
        remaining -= len(chunk)


def skip_forward(stream: BinaryIO, count: int) -> None:
    """Read and discard exactly ``count`` bytes from a forward-only ``stream``.

    Raises :class:`EOFError` if the stream ends before ``count`` bytes are consumed.
    """
    skipped = 0
    for n in _drain_chunks(stream, count):
        skipped += n
    if skipped < count:
        raise EOFError("stream ended before the requested position")


class _MemberSlice(ReadOnlyIOStream):
    """One member's forward-only view over its owning reader's block stream.

    Non-owning: closing a member slice never closes the shared block — the
    :class:`SolidBlockReader` owns that. Reads are bounded to the member's declared size
    and flow through the reader so it always knows the block's position.

    When constructed with ``pending=True`` (``open_member(..., lazy=True)``), the
    skip-to-``offset`` runs on first read rather than at construction. Closing a
    slice never advances or drains the block — pending *and* already-positioned
    unread closes are free. The gap before the next member is consumed by the
    next ``open_member`` (or a pending slice's first read).
    """

    def __init__(
        self,
        reader: SolidBlockReader,
        offset: int,
        size: int,
        *,
        pending: bool = False,
    ) -> None:
        super().__init__()
        self._reader = reader
        self._offset = offset
        self._size = size
        self._remaining = size
        self._pending = pending
        self._seq = reader._stamp()

    def _ensure_positioned(self) -> None:
        if not self._pending:
            return
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        self._reader._advance_to(self)
        self._pending = False

    def read(self, n: int = -1, /) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        self._ensure_positioned()
        # close() clears _current without marking slices closed. Check that
        # before the superseded test, or eager read() claims a later open_member()
        # that never happened. Lazy already raises here via _ensure_positioned.
        if self._reader._closed:
            raise ValueError("SolidBlockReader is closed")
        if self._reader._current is not self:
            raise ValueError("solid member superseded by a later open_member()")
        if self._remaining <= 0:
            return b""
        if n < 0 or n > self._remaining:
            n = self._remaining
        data = self._reader._consume(n)
        self._remaining -= len(data)
        return data

    def tell(self) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._reader._closed:
            raise ValueError("SolidBlockReader is closed")
        if self._pending:
            return 0
        if self._reader._current is not self:
            raise ValueError("solid member superseded by a later open_member()")
        return self._size - self._remaining


class SolidBlockReader:
    """Vend consecutive member sub-streams over one forward-only decoded block.

    ``open_member(offset, size)`` returns a forward-only stream for the member occupying
    ``[offset, offset + size)`` of the decoded block. Members must be opened in
    non-decreasing ``offset`` order; the reader skips forward from its current position to
    ``offset`` lazily, at open time, so a partially-read (or unread) member costs nothing
    until the next one is requested. Only one member is active at a time.

    Pass ``lazy=True`` to defer that open until the first read on the returned handle
    (claim-time still rejects ``offset`` behind the current position). Closing a lazy
    handle without reading never skip-decodes. Eager and lazy both return the same
    :class:`_MemberSlice` type — no extra wrapper layer.

    Closed / superseded / same-offset guards on the returned handle are
    defence-in-depth for this primitive. Current backends serialize member access via
    ``_drive_pass_streams(close_previous=True)``, so no public path holds two live
    slices at once.
    """

    def __init__(self, block: BinaryIO, *, close_block: bool = True) -> None:
        self._block = block
        self._close_block = close_block
        self._pos = 0  # bytes consumed from the block so far
        self._current: _MemberSlice | None = None
        self._closed = False
        self._seq = 0

    def _stamp(self) -> int:
        self._seq += 1
        return self._seq

    def _claim_offset(self, offset: int) -> None:
        if self._closed:
            raise ValueError("SolidBlockReader is closed")
        if offset < self._pos:
            raise ValueError(
                f"solid members must be opened in order: offset {offset} < "
                f"position {self._pos}"
            )

    def _advance_to(self, member: _MemberSlice) -> None:
        """Position the block at ``member`` and make it current.

        Shared by eager ``open_member`` and a pending slice's first read.
        Uses :meth:`_skip_to` so a raising mid-skip still credits ``_pos``.
        """
        self._claim_offset(member._offset)
        current = self._current
        # Offsets alone cannot order two slices at the same offset (a zero-size
        # member sharing its successor's start). A strictly newer claim wins.
        if current is not None and current is not member and current._seq > member._seq:
            raise ValueError("solid member superseded by a later open_member()")
        self._current = None
        self._skip_to(member._offset)
        self._current = member

    def open_member(self, offset: int, size: int, *, lazy: bool = False) -> BinaryIO:
        if lazy:
            self._claim_offset(offset)
            return _MemberSlice(self, offset, size, pending=True)
        slice_ = _MemberSlice(self, offset, size, pending=False)
        self._advance_to(slice_)
        return slice_

    def _skip_to(self, offset: int) -> None:
        """Advance the block to ``offset``, crediting every discarded byte to ``_pos``.

        Credit is per yielded chunk, not after the whole skip. A raising ``read``
        (7z folder decode, ``unrar`` pipe) then leaves ``_pos`` matching bytes
        already pulled.
        """
        remaining = offset - self._pos
        for n in _drain_chunks(self._block, remaining):
            self._pos += n
            remaining -= n
        if remaining > 0:
            raise EOFError("stream ended before the requested position")

    def _consume(self, n: int) -> bytes:
        data = self._block.read(n)
        self._pos += len(data)
        return data

    def close(self) -> None:
        # No draining: whatever is left in the block is discarded with it.
        if self._closed:
            return
        self._closed = True
        self._current = None
        if self._close_block:
            self._block.close()

    def __enter__(self) -> SolidBlockReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
