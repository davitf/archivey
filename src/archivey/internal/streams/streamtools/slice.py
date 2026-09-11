"""Bound views over a region of another binary stream.

Used to present a member's byte range inside a container as a standalone stream, and (via
``fix_stream_start_position``) to give a mid-positioned stream a clean ``tell() == 0``
origin for codec libraries that assume it.

Two construction contracts (two classes, shared bound/tell/size arithmetic):

1. :class:`SlicingStream` — **single-consumer**. ``read`` continues from wherever the
   underlying handle currently sits. Correct when only one view is live. Construction
   does not seek the handle to ``start``; the first ``read``/``seek`` positions it.
2. :class:`SharedView` — **locked, re-seek-before-read**. Every ``read`` does
   ``seek(start + _pos); read(n)`` under the lock so interleaved views never clobber
   each other. Construction does not call unlocked ``tell``/``seek`` on the shared
   handle — ``BufferedReader.tell`` is not thread-safe. A cheap size probe (to clamp
   ``length``) runs under the lock and restores the handle.

``SharedSource.view`` returns :class:`SharedView`. Direct ``SlicingStream(..., lock=…)``
is gone; pass ``lock`` to :class:`SharedView`.
"""

from __future__ import annotations

import io
from contextlib import nullcontext
from typing import BinaryIO, Callable, ContextManager

from archivey.internal.streams.streamtools.base import ReadOnlyIOStream
from archivey.internal.streams.streamtools.binaryio import (
    is_seekable,
    read_exact,
    read_full_count,
    source_byte_size,
)


def _clamp_slice_length(
    start: int | None, length: int | None, source_size: int | None
) -> int | None:
    """Cap a slice to the source's cheap size.

    When ``source_size`` is known, an omitted ``length`` becomes the remaining bytes
    and an over-long ``length`` is truncated. The source is assumed not to grow —
    the result is frozen at construction. When size is unknowable, ``length`` is
    returned unchanged (including ``None`` for an open-ended slice).
    """
    if source_size is None or start is None:
        return length
    available = max(source_size - start, 0)
    if length is None:
        return available
    return min(length, available)


class SlicingStream(ReadOnlyIOStream):
    """A single-consumer view over ``[start, start+length)`` of an underlying stream.

    Seekable underlying stream:
      - ``start`` is the absolute offset where the slice begins (default: the stream's
        current position, observed via ``tell()`` — that does not move the handle).
      - ``length`` caps the slice (default: to the end of the underlying stream).
      - Seeking is relative to the start of the slice.
      - Construction does **not** seek the handle to ``start``. The first ``read`` or
        ``seek`` does. A cheap ``source_byte_size`` probe (tell / SEEK_END / restore)
        may run so an over-declared ``length`` can be clamped; the handle is left
        where the caller had it.

    ``read(n)`` is **full-count over a full-count inner** (ADR 0014): it coalesces with
    ``read_full_count``, so it returns ``n`` bytes unless the slice ends, the inner ends,
    or the inner signals a terminal boundary with a short return. It deliberately does
    *not* keep pulling past a short — that would collapse a decoder's deliver-then-raise
    truncation shape. A raw inner that shorts mid-stream needs a buffer in front.

    Non-seekable underlying stream:
      - ``start`` must be ``None`` (the slice begins at the current position).
      - ``length`` caps how many bytes may be read; seeking is unsupported.

    Optional ``check_open``: called at the start of I/O; raise to signal a closed source
    (SharedSource uses this so a closed factory poisons its views).

    It is a :class:`ReadOnlyIOStream`, not a :class:`DelegatingStream`: every operation is
    *transformed*, not forwarded — ``read`` clamps to the slice bounds, and ``seek``/``tell``
    are relative to the slice start, not the underlying offset. And by default it is a
    *non-owning view*: it does NOT close the underlying stream (the container owns it), whereas
    ``DelegatingStream.close`` closes its inner. So delegation would be both useless (almost
    everything is overridden) and unsafe (the close default). The opt-in ``own_source`` flag
    flips just the close behaviour for the case where the view is the sole owner of a private
    underlying stream (e.g. a per-member decoder); it never applies to a ``SharedSource`` view.

    It also does not expose ``name``: a slice view remaps the origin, so forwarding the
    underlying path would mislead libraries that reopen or stat by ``stream.name``.
    """

    def __init__(
        self,
        stream: BinaryIO,
        start: int | None = None,
        length: int | None = None,
        *,
        check_open: Callable[[], None] | None = None,
        own_source: bool = False,
    ) -> None:
        super().__init__()
        self._init_from_source(
            stream,
            start,
            length,
            io_guard=nullcontext(),
            seek_before_read=False,
            check_open=check_open,
            own_source=own_source,
        )

    def _init_from_source(
        self,
        stream: BinaryIO,
        start: int | None,
        length: int | None,
        *,
        io_guard: ContextManager[object],
        seek_before_read: bool,
        check_open: Callable[[], None] | None,
        own_source: bool,
    ) -> None:
        self._stream = stream
        # A view is non-owning by default (never closes the underlying — the container
        # owns it). ``own_source=True`` is the opt-in for the case where this view is the
        # sole owner of a private underlying stream (e.g. a per-member decoder opened just
        # for this slice) that should be closed together with the view.
        self._own_source = own_source
        self._seekable = is_seekable(stream)
        self._io_guard = io_guard
        self._seek_before_read = seek_before_read
        self._source_check = check_open
        self._pos = 0  # position relative to the start of the slice

        if not self._seekable:
            if start is not None:
                raise ValueError(
                    "Cannot slice a non-seekable stream with a start position"
                )
            self._start = None
            self._length = length
            self._unpositioned = False
            return

        # Resolve start and clamp length. For a locked view this block holds the
        # lock so a cheap size probe never unlocked-tells a BufferedReader.
        # Single-consumer: the probe restores the caller's position; we do not
        # seek to ``start`` here (first read/seek does).
        with self._io_guard:
            if start is None:
                start = stream.tell()
            source_size = source_byte_size(stream)
        self._start = start
        self._length = _clamp_slice_length(start, length, source_size)
        # Locked views re-seek on every read, so they are never "unpositioned".
        self._unpositioned = not self._seek_before_read

    def _raise_if_closed(self) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._source_check is not None:
            self._source_check()

    def _compute_bytes_to_read(self, n: int) -> int:
        if self._length is not None:
            remaining = self._length - self._pos
            if n < 0:
                return max(remaining, 0)
            return min(n, max(remaining, 0))
        return n

    def read(self, n: int = -1, /) -> bytes:
        self._raise_if_closed()
        # Three gather policies, and the difference matters (ADR 0014):
        #
        # * Sized ``read(n)`` — ``read_full_count``: keep asking while each piece returns
        #   the full ask, but **stop on the first short non-empty return**. A short return
        #   from an inner is a terminal signal, not "ask again": a decoder with deferred
        #   truncation (this view sits directly over one in the 7z member and LZMA
        #   ``cap_size`` paths) hands back the recoverable prefix now and raises on the
        #   *next* empty read, and ``read_exact`` here would pull that ``TruncatedError``
        #   into this call and drop the prefix.
        # * Bounded ``read(-1)`` — ``read_exact``: the caller asked for the whole slice and
        #   will not call again (``test_bounded_drain_pulls_deferred_truncation``).
        # * Unbounded ``read(-1)`` — no count to fill; pass through.
        #
        # None of this rescues a RawIO that shorts mid-stream; per ADR 0014 that inner
        # "needs a buffer in front", which is what ``ensure_full_count_reads`` puts at the
        # source boundary. Every inner a backend slices is full-count already.
        drain = n < 0
        n = self._compute_bytes_to_read(n)  # stays negative for an unbounded drain
        if n == 0:
            return b""
        with self._io_guard:
            # Recheck under the lock: another thread may have closed the source
            # (or this view) after the outer check and before we I/O.
            self._raise_if_closed()
            # Re-seek mode: reposition to this view's absolute offset so interleaved
            # views never clobber each other. Single-consumer: the first I/O seeks
            # to start (construction left the handle where the caller had it); later
            # reads continue from wherever the handle sits.
            if self._seek_before_read or self._unpositioned:
                assert (
                    self._start is not None
                )  # re-seek / lazy-position views are seekable
                self._stream.seek(self._start + self._pos)
                self._unpositioned = False
            if not drain:
                data = read_full_count(self._stream, n)
            elif self._length is None:
                data = self._stream.read(n)
            else:
                data = read_exact(self._stream, n)
            self._pos += len(data)
            return data

    def tell(self, /) -> int:
        self._raise_if_closed()
        return self._pos

    def nearest_resume_offset(self, target: int) -> None:
        """Decline the rewind-cost question: this view has its own offset space.

        Offsets here are relative to ``start``, while an inner seek-point table is in
        the inner's space, so forwarding would report a distance against the wrong
        origin. ``None`` means "no cost signal". Kept as an explicit decline so a
        future slice-like wrapper does not quietly acquire forwarding; Parcel B
        removes it.
        """
        return

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        self._raise_if_closed()
        if not self._seekable:
            raise io.UnsupportedOperation("seek on non-seekable stream")

        assert self._start is not None  # always set for seekable streams
        start_abs = self._start

        if whence == io.SEEK_SET:
            new_relative = offset
        elif whence == io.SEEK_CUR:
            new_relative = self._pos + offset
        elif whence == io.SEEK_END:
            if self._length is None:
                # No declared length: the slice ends where the underlying stream does,
                # so probe that end on demand.
                with self._io_guard:
                    self._raise_if_closed()
                    end_relative = self._stream.seek(0, io.SEEK_END) - start_abs
            else:
                end_relative = self._length
            new_relative = end_relative + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")

        if new_relative < 0:
            # Match BytesIO: a relative seek (SEEK_CUR/SEEK_END) that underflows clamps
            # to the origin; only an explicitly negative SEEK_SET raises. Callers probing
            # backwards from the end (e.g. ZipFile's ``seek(-22, SEEK_END)`` EOCD probe
            # on a short source) rely on the clamp rather than a raw ``ValueError``.
            if whence == io.SEEK_SET:
                raise ValueError("Negative seek position")
            new_relative = 0

        # Seeking past a defined end is allowed (reads clamp to empty), matching BytesIO.
        if not self._seek_before_read:
            # Single-consumer: keep the underlying handle in sync with the view.
            self._stream.seek(start_abs + new_relative)
            self._unpositioned = False
        # Re-seek mode: only update _pos — the next read re-seeks under the guard.
        self._pos = new_relative
        return self._pos

    def seekable(self) -> bool:
        return self._seekable

    def independent_view(self) -> SlicingStream:
        """A fresh, position-isolated sibling over the same region, lock, and source.

        Only valid on a :class:`SharedView`: the sibling shares the underlying
        handle + lock + bounds but keeps its own ``_pos``, so a second consumer (e.g. a
        truncation scan running while the accelerator holds another view mid-stream) re-seeks
        under the same lock and never clobbers this view's cursor. A single-consumer view has
        no lock to coordinate on, so this raises there.
        """
        raise io.UnsupportedOperation(
            "independent_view requires a locked SharedSource view"
        )

    def close(self) -> None:
        # Non-owning by default: mark this view closed only. With ``own_source`` the view
        # owns a private underlying stream and closes it too.
        if not self.closed:
            if self._own_source:
                self._stream.close()
            super().close()

    @property
    def size(self) -> int | None:
        """Total slice length when cheaply knowable (the fsspec-style ``size`` convention).

        A declared (and construction-clamped) ``length`` answers directly; an open-ended
        slice derives it from the underlying stream's cheap size (``source_byte_size``),
        and reports ``None`` when that is unknowable — never by an expensive end-seek.
        """
        if self._length is not None:
            return self._length
        if self._start is None:
            return None  # non-seekable underlying stream: length unknowable cheaply
        underlying = source_byte_size(self._stream)
        if underlying is None:
            return None
        return max(underlying - self._start, 0)


class SharedView(SlicingStream):
    """Locked re-seek-before-read view over ``[start, start+length)``.

    Every ``read`` does ``seek(start + _pos); read(n)`` under ``lock`` so interleaved
    views never clobber each other. ``seek`` only updates this view's ``_pos``; the
    next ``read`` re-seeks.

    Construction does not call unlocked ``tell``/``seek`` on the shared handle.
    A missing ``start`` is read from ``tell()`` under the lock. A cheap size probe
    (to clamp ``length``) also runs under the lock and restores the handle.
    Unlocked ``BufferedReader.tell`` under concurrency corrupts the buffer even when
    every later ``read`` is locked.

    The source must be seekable: every ``read`` re-seeks. A non-seekable stream
    is a ``ValueError`` at construction, matching :class:`SharedSource`.
    """

    def __init__(
        self,
        stream: BinaryIO,
        start: int | None = None,
        length: int | None = None,
        *,
        lock: ContextManager[object],
        check_open: Callable[[], None] | None = None,
        own_source: bool = False,
    ) -> None:
        if not is_seekable(stream):
            raise ValueError("SharedView requires a seekable stream")
        # Skip SlicingStream.__init__: that path is the single-consumer contract
        # (nullcontext, lazy-position). ReadOnlyIOStream sets the RawIOBase closed flag.
        ReadOnlyIOStream.__init__(self)
        self._init_from_source(
            stream,
            start,
            length,
            io_guard=lock,
            seek_before_read=True,
            check_open=check_open,
            own_source=own_source,
        )

    def independent_view(self) -> SharedView:
        return SharedView(
            self._stream,
            start=self._start,
            length=self._length,
            lock=self._io_guard,
            check_open=self._source_check,
        )


def fix_stream_start_position(stream: BinaryIO) -> BinaryIO:
    """Make a stream behave as if its current position were 0.

    If ``stream`` is seekable and already at offset 0, it is returned unchanged. If it is
    seekable but positioned mid-stream, it is wrapped in a :class:`SlicingStream` so the
    consumer (a codec library that assumes ``tell() == 0``) sees a clean origin.
    Non-seekable streams are returned as-is (the caller can only read forward anyway).
    """
    if not is_seekable(stream):
        return stream
    start_pos = stream.tell()
    if start_pos == 0:
        return stream
    return SlicingStream(stream, start=start_pos)
