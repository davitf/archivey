"""The archive-source boundary: full-count ``read(n)``, and a stream archivey may not close.

``io.RawIOBase.read(n)`` may legally return short. Some header parsers issue one
``read(n)`` and raise or treat a short as EOF, so every archive source is made
full-count here before it reaches a backend. Seekable sources get
``io.BufferedReader`` (readahead is recoverable by seeking). Non-seekable sources
that are not already a CPython buffer get :class:`FullCountStream`, which
re-asks for the missing bytes and holds no buffer of its own.

The boundary also decides ownership, because it is the last place that still knows
which object the *caller* handed in. A source that needs neither wrapper — a
``BytesIO``, an ``open()`` handle — used to be passed through as itself, and could
then end up inside an owning wrapper downstream and be closed. It gets
:class:`BorrowedStream` instead, so nothing archivey builds on top of a source has
the caller's object as its inner.

Lives in its own module so :mod:`.binaryio` (helpers) and :mod:`.base`
(``ReadOnlyIOStream``) stay one-way: this module imports both, neither
imports this.
"""

from __future__ import annotations

from typing import BinaryIO, cast

from archivey.internal.streams.streamtools.base import (
    DelegatingStream,
    ReadOnlyIOStream,
)
from archivey.internal.streams.streamtools.binaryio import (
    _BUFFER_TYPES,
    ensure_bufferedio,
    is_seekable,
    raise_if_text_stream,
    read_exact,
    source_name,
)


class FullCountStream(ReadOnlyIOStream):
    """Make a short-returning inner full-count without reading ahead.

    ``io.RawIOBase.read(n)`` may legally return short of ``n``. Some header
    parsers issue one ``read(n)`` and raise or treat a short as EOF, so this
    wrapper re-asks for the bytes still missing. That is :func:`read_exact`
    (a short means "ask again"), not a single forwarded ``inner.read(n)``
    (a short means terminal) — the two gather policies are enumerated in
    ``slice.py``, per ADR 0014. ``BufferedReader`` would also be full-count,
    but it reads *ahead*; from a pipe that over-read is unrecoverable at a
    boundary that hands the source between layers. This class holds no buffer
    of its own: a ``read(n)`` takes exactly ``n`` bytes from the inner (or
    everything up to EOF). Full-count also means blocking: ``read(n)`` waits
    until ``n`` bytes have arrived or the source ends. Every layer above
    already looped to the same effect; the wrapper moves that wait down one
    level, it does not add one.

    :class:`ReadOnlyIOStream` is the base so ``readinto`` routes through this
    ``read`` and inherits the guarantee. :class:`DelegatingStream` would pass
    ``readinto`` straight to the short-returning inner. ``seekable()`` stays
    ``False`` and ``tell()`` stays raising — this does not convert a pipe.

    The inner is the caller's object, un-normalised: this module runs
    ``raise_if_text_stream`` and the two ``isinstance`` short-circuits, never
    ``ensure_binaryio``. The one assumption is that ``read(n)`` for ``n > 0``
    returns at most ``n`` bytes, and empty only at EOF. Both halves are
    enforced: fewer than ``n`` is gathered with :func:`read_exact`; more than
    ``n`` raises.

    Nothing beyond that sentence is safe to assume. ``readall()`` is a
    ``RawIOBase`` method — ``BufferedReader``, ``BytesIO`` and ``GzipFile``
    have none, and ``typing.BinaryIO`` does not declare it. An inner that
    reaches here can lack it outright: ``io.BufferedRWPair`` over a pipe is
    non-seekable, is not in ``_BUFFER_TYPES``, and has no ``readall``.
    ``read(-1)`` is only documented to drain because ``RawIOBase.read``
    dispatches to ``readall()``; a class that overrides ``read`` bypasses
    that dispatch, and a non-blocking raw returns short or ``None`` either
    way. The drain therefore calls ``self.readall()`` —
    :class:`ReadOnlyIOStream`'s, issuing sized reads back through this
    class's own ``read`` — instead of asking the inner to drain itself.

    ``peel_for_source_size`` is the existing opt-in for a pass-through wrapper
    whose cheap size *is* the inner's. ``fileno`` is not forwarded.
    """

    peel_for_source_size: bool = True

    def __init__(self, inner: BinaryIO) -> None:
        super().__init__()
        self._inner = inner

    def read(self, n: int = -1, /) -> bytes:
        if n is None or n < 0:
            # ReadOnlyIOStream.readall loops sized self.read(n). Never the
            # inner's readall()/read(-1) — an inner may have no readall at all
            # (BufferedRWPair, or GzipFile over a pipe).
            return self.readall()
        data = self._inner.read(n)
        got = len(data)
        if got == n:
            return data  # common case: no copy
        if got > n:
            raise ValueError(
                f"inner returned {got} bytes for read({n}): {self._inner!r}"
            )
        return data + read_exact(self._inner, n - got)

    def close(self) -> None:
        # Do not close the inner. This is the source-boundary wrapper: the
        # caller owns the stream, same as BinaryIOWrapper / _NonClosingBufferedReader.
        super().close()

    @property
    def name(self) -> str:  # pyrefly: ignore[bad-override]  # base is Never; this returns a path when the inner has one
        """Path of the inner stream, or raise :exc:`AttributeError` if it has none.

        ``source_name`` has no peel path, so this has to forward. Raising keeps
        ``hasattr(..., "name")`` false when the inner is nameless.
        """
        resolved = source_name(self._inner)
        if resolved is not None:
            return resolved
        raise AttributeError("name")

    def __repr__(self) -> str:
        return f"FullCountStream({self._inner!r})"


class BorrowedStream(DelegatingStream):
    """A caller's stream, wrapped so archivey's close chains stop here.

    ``archive-reading`` says archivey never closes a caller-supplied ``BinaryIO``,
    and the stream layer's borrow-by-default rule is how that is kept. But the rule
    only covers the wrappers that borrow: a caller's own object reaching a backend
    unwrapped is one owning wrapper away from being closed, and that is what used to
    happen — a measured ZIP or compressed TAR opened from a ``BytesIO`` or an
    ``open()`` handle closed it, because ``SeekCountingStream`` sits in the close
    chain and owns its inner. Nothing in the layer was wrong; the caller's object was
    simply inside it.

    So the boundary hands every backend a wrapper instead. Everything is forwarded —
    ``read`` / ``readinto`` (zero-copy), ``seek`` / ``tell`` / ``seekable``,
    ``name``, ``fileno``, and the cheap size through ``peel_for_source_size`` — so a
    backend sees what it saw before. ``close`` is the single exception: it marks this
    wrapper closed and stops. Whatever closes it was closing the caller's stream
    before, and there is no keyword anywhere upstream that has to be right for that
    to hold.

    Ordering: the wrapper goes *outside* the full-count normalisation, never inside.
    Inside, :func:`ensure_full_count_reads` would no longer see the caller's
    ``BufferedReader`` for what it is and would put a second buffer in front of it,
    reading ahead over a source that may be a pipe.
    """

    # A pure pass-through: the inner's cheap size is this stream's size, so
    # ``source_byte_size`` must peel it (a BytesIO source keeps answering from
    # ``getbuffer()``, an open file from ``fstat``).
    peel_for_source_size: bool = True
    # The reason the class exists. See DelegatingStream's "Close ownership".
    owns_inner: bool = False

    def fileno(self) -> int:
        """Forward ``fileno`` — unlike a transforming wrapper, this one is the inner.

        ``ensure_full_count_reads`` used to return a caller's ``BufferedReader``
        unchanged rather than "drop ``fileno()`` for no gain"; forwarding keeps that
        true now that it is wrapped.
        """
        return self._inner.fileno()

    def __repr__(self) -> str:
        return f"BorrowedStream({self._inner!r})"


def ensure_full_count_reads(stream: BinaryIO) -> BinaryIO:
    """Normalise a caller's stream at the source boundary: full-count, and borrowed.

    ``io.RawIOBase.read(n)`` is an *up-to-n* contract and real sources use the
    latitude, but some header parsers — archivey's own and the stdlib's
    (``zipfile``/``tarfile``/``pycdlib``) — pull a fixed-size structure with one
    ``read(n)`` and read anything shorter as EOF, so a healthy archive from a
    short-returning source was reported as corrupt.

    A **seekable** source is wrapped in ``io.BufferedReader``, whose ``read``
    promises the full count. Its readahead is bounded and recoverable — the
    over-read stays in the buffer and the source can be repositioned anyway — and
    it also collapses the parsers' many tiny reads. Already-buffered sources
    (``open()``'s ``BufferedReader``, ``BytesIO``) pay nothing.

    A **non-seekable** source that is not already a CPython buffer is wrapped
    in :class:`FullCountStream`, which gathers by re-asking for the bytes still
    missing and holds no buffer of its own. The inner is the caller's object,
    un-normalised — never ``ensure_binaryio`` — so the wrapper cannot assume
    ``readall`` or a draining ``read(-1)``. A ``read(n)`` on the returned
    stream takes exactly ``n`` bytes from the source; ``seekable()`` stays
    ``False``. ``BufferedReader`` is not used on this branch because it reads
    *ahead*, and from a pipe that over-read is unrecoverable —
    ``_NonClosingBufferedReader`` detaches on close and strands whatever it
    pulled. Some objects also lie about ``seekable()``: a Windows pipe reports
    ``True`` while ``seek()`` does not reposition; :func:`is_seekable` treats
    FIFO and char-device fds as non-seekable regardless.

    An already-present ``io.BufferedReader`` / ``io.BufferedRandom`` keeps reading
    for itself on both branches: it is already full-count, and the buffer is the
    caller's, so nothing is stacked in front of it. (That is not the read-ahead
    hazard above, which is archivey *adding* a buffer it then detaches.) It is not
    returned as itself, though — see below.

    **Ownership.** Whatever the branch, what comes back is never the caller's own
    object. The two wrappers above already borrow, and the sources that need
    neither — an ``open()`` handle, a ``BytesIO`` — get :class:`BorrowedStream`,
    which forwards everything (``fileno`` included, so passing a buffer through
    costs nothing it used to) and closes nothing. Before that, those two shapes
    reached backends as themselves and a measured ZIP or compressed TAR closed
    them: ``SeekCountingStream`` is a ``DelegatingStream``, and those own their
    inner. Which wrapper a backend puts on a source is the backend's business; that
    it is not putting it on the caller's object is decided here, once.

    Codec layers above this boundary may still buffer: a
    ``DecompressorStream`` owns its input to EOF, so its ``BufferedReader`` is
    safe there. This function is the boundary; it is not.

    The function is idempotent: its own two wrappers are returned unchanged, so
    callers (notably ``PeekableStream``) can apply it defensively.

    Why the boundary is here, and the listing-amplification measurement, live
    in the archived ``short-read-source-contract`` change.
    """
    raise_if_text_stream(stream)
    if isinstance(stream, (FullCountStream, BorrowedStream)):
        # Already past this boundary. BorrowedStream is only ever built here, over
        # a stream this function had just made full-count, so returning it unchanged
        # keeps the defensive callers (PeekableStream) from stacking a second layer.
        return stream
    # Same buffer types ``is_seekable`` peels — a caller's BufferedReader is
    # already full-count, so it only needs the borrow wrapper, whose fileno()
    # forwards.
    if isinstance(stream, _BUFFER_TYPES):
        return BorrowedStream(stream)
    if not is_seekable(stream):
        # FullCountStream does not close its inner either, so the borrow wrapper
        # would add a layer and no guarantee.
        return FullCountStream(stream)
    buffered = ensure_bufferedio(stream)
    if buffered is stream:
        # An already-buffered seekable source (a BytesIO): full-count as it stands,
        # so the borrow wrapper is all it needs.
        return BorrowedStream(cast("BinaryIO", buffered))
    # _NonClosingBufferedReader detaches rather than closing, so the caller's raw
    # stream is already out of the close chain.
    # BufferedIOBase is a BinaryIO at runtime; typeshed models the two separately.
    return cast("BinaryIO", buffered)
