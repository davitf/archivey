"""Full-count ``read(n)`` at the archive-source boundary.

``io.RawIOBase.read(n)`` may legally return short. Some header parsers issue one
``read(n)`` and raise or treat a short as EOF, so every archive source is made
full-count here before it reaches a backend. Seekable sources get
``io.BufferedReader`` (readahead is recoverable by seeking). Non-seekable sources
that are not already a CPython buffer get :class:`FullCountStream`, which
re-asks for the missing bytes and holds no buffer of its own.

Lives in its own module so :mod:`.binaryio` (helpers) and :mod:`.base`
(``ReadOnlyIOStream``) stay one-way: this module imports both, neither
imports this.
"""

from __future__ import annotations

from typing import BinaryIO, cast

from archivey.internal.streams.streamtools.base import ReadOnlyIOStream
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


def ensure_full_count_reads(stream: BinaryIO) -> BinaryIO:
    """Make a source's ``read(n)`` return the full count short of EOF.

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

    An already-present ``io.BufferedReader`` / ``io.BufferedRandom`` is
    returned unchanged on both branches: it is already full-count, and the
    buffer is the caller's — wrapping it would drop ``fileno()`` and add a
    Python layer the source does not need. That is not the read-ahead hazard
    above, which is archivey *adding* a buffer it then detaches.

    Codec layers above this boundary may still buffer: a
    ``DecompressorStream`` owns its input to EOF, so its ``BufferedReader`` is
    safe there. This function is the boundary; it is not.

    The function is idempotent: a :class:`FullCountStream` or CPython buffer
    argument is returned unchanged, and the seekable branch already
    short-circuits on ``io.BufferedIOBase``. Callers (notably
    ``PeekableStream``) can therefore apply it defensively.

    Why the boundary is here, and the listing-amplification measurement, live
    in the archived ``short-read-source-contract`` change.
    """
    raise_if_text_stream(stream)
    # Same buffer types ``is_seekable`` peels — a caller's BufferedReader is
    # already full-count; wrapping it would drop fileno() for no gain.
    if isinstance(stream, FullCountStream) or isinstance(stream, _BUFFER_TYPES):
        return cast("BinaryIO", stream)
    if not is_seekable(stream):
        return FullCountStream(stream)
    # BufferedIOBase is a BinaryIO at runtime; typeshed models the two separately.
    return cast("BinaryIO", ensure_bufferedio(stream))
