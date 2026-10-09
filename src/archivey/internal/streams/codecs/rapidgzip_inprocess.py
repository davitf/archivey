"""The guard around rapidgzip's in-process decoder (bzip2): close-on-finalize, and the
trap that keeps a source fault from aborting the process.
"""

from __future__ import annotations

import io
import os
import weakref
from collections.abc import Callable
from typing import TYPE_CHECKING, BinaryIO

from archivey.exceptions import ReadError
from archivey.internal.streams.codecs.base import CodecSource
from archivey.internal.streams.rapidgzip_child import mark_callers_source
from archivey.internal.streams.streamtools import (
    DelegatingStream,
    ensure_binaryio,
)

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer


class _AcceleratorStream(DelegatingStream):
    """Wrap a threaded accelerator (``rapidgzip``) so its underlying object is always *closed*
    before it is freed, and so a fault the :class:`_TrappingSource` parked is re-raised after
    each read / readinto / seek.

    A read that raises moves the decoder back to where it started, so ``tell()`` stays at
    the bytes the caller received. When that is not possible, or the caller's source
    faulted during a read or seek, the stream is given up for good: every later read,
    readinto, seek or ``tell()`` raises :class:`ReadError` naming the cause, and ``close()``
    still works. See :meth:`_after_failed_read`.

    The accelerators spawn C++ ``std::thread``s (invisible to Python's ``threading`` module).
    A worker thread still running when the interpreter finalizes aborts the process with
    SIGABRT ("Detected Python finalization from running … thread" → "terminate called").
    Crucially, ``join_threads()`` does **not** stop the thread — only ``close()`` does (the
    libraries' own message says to "close all … objects"). So an object that is merely joined,
    or that is finalized by the garbage collector without being closed — which happens when a
    corrupt/truncated read raises and the exception traceback captures the stream in a reference
    cycle, where finalizer ordering is undefined — still trips the abort.

    A :func:`weakref.finalize` guard closes that window: it ``close()``s the raw object exactly
    once, when this wrapper is collected (cyclically or not) or at interpreter exit, whichever
    comes first, holding a strong reference to the raw object so the close always runs *before*
    that object is freed. ``close()`` on the wrapper simply triggers the same guard early. This
    guard lives at the codec's object-creation point (not in the outer ``ArchiveStream``) because
    a raw accelerator object can also be produced via ``backend.open()`` with no ``ArchiveStream``
    around it — the guard must attach where the object is born.
    """

    _SUBCLASS_CLOSES_INNER = True

    def __init__(self, inner: object, *, trap: _TrappingSource | None = None) -> None:
        super().__init__(ensure_binaryio(inner))
        # The finalize callback must NOT reference self — a bound method would pin the wrapper
        # and defeat GC-time finalization — so it takes the raw inner and lives as a staticmethod.
        self._finalize = weakref.finalize(self, self._close_inner, self._inner, trap)
        # Bug 3 containment: when rapidgzip reads a caller-owned Python source through a
        # ``_TrappingSource``, a source-side fault is swallowed into ``trap`` (so it never
        # crosses into rapidgzip's C++ and aborts the process) and re-raised here after each
        # accelerator call, as a normal Python exception.
        self._trap = trap
        # Why the stream was given up, once a call left the decoder at a position that
        # matches no byte the caller received; see _after_failed_read.
        self._lost: str | None = None

    @staticmethod
    def _close_inner(inner: BinaryIO, trap: _TrappingSource | None) -> None:
        # close() — not join_threads() — stops the C++ worker thread, and must run before the
        # interpreter finalizes or the process aborts. Best-effort; the guard runs it once.
        try:
            inner.close()
        except Exception:  # noqa: BLE001 - best-effort; the object is going away regardless
            pass
        if trap is not None:
            trap.release()

    def _reraise_trapped(self) -> None:
        # Surface a fault the source shim parked, after the accelerator call that observed
        # it. read/readinto/seek re-check after every call, and also when the accelerator
        # raised: the shim's EOF-shaped answer often makes the accelerator raise its own
        # error ("Unexpected end of file"), and the parked fault is the real one. A fault
        # parked while the accelerator opens is re-raised by _open_accelerator, so none
        # reaches the caller as data. The parked fault wins only over an ``Exception``:
        # an interrupt raised during the call propagates as itself, and the fault stays
        # parked for the next boundary, so neither is lost.
        #
        # close() deliberately does not drain it. Past the open, a fault is parked only by
        # a source read that no caller call waits on: a background worker's prefetch. That
        # runs on a worker thread, so it is never a KeyboardInterrupt (Python delivers
        # those to the main thread only), and a caller that closes without reading more
        # wants no error from a prefetch it never asked for.
        _raise_parked(self._trap)

    def nearest_resume_offset(self, target: int) -> int | None:
        """Decompressed offset the accelerator would restart from to reach ``target``.

        An engaged accelerator is not automatically cheap: measured against rapidgzip
        0.16, ``gzip.compress`` of 5 MB of random data yields three block offsets
        (0, ~4.2 MB, 5 MB), so a backward seek into the first gap discards megabytes of
        decoded progress. That is the same event as a single-block ``.xz`` rewind, which
        is why the predicate is this distance rather than "an accelerator is present".

        ``available_block_offsets()`` (~0.01 ms) rather than ``block_offsets()``: the
        latter forces the *complete* index, so asking the cost would change it. A partial
        index reports a resume point further back, which errs toward telling the caller.
        """
        known = self.available_block_offsets()
        preceding = [value for value in known.values() if value <= target]
        if not preceding:
            return None
        return max(preceding)

    def available_block_offsets(self) -> dict[int, int]:
        """The part of the decoder's index built so far, compressed bit offset to
        decompressed offset; empty when it has none or the query fails.

        Unlike :meth:`block_offsets` it forces nothing, and it still answers after a
        read failed: measured on rapidgzip 0.16's bzip2 decoder, a stream cut in half
        lists every block it decoded ahead of the reader before the error.
        """
        offsets = getattr(self._inner, "available_block_offsets", None)
        if offsets is None:
            return {}
        try:
            return dict(offsets())
        # Empty is safe for both callers: the rewind diagnostic then reports a point
        # further back, and a takeover starts at the origin.
        except Exception:  # noqa: BLE001 - see the comment above
            return {}

    def compressed_position(self) -> int | None:
        """How far into the source the decoder has read, in whole bytes, if it says.

        rapidgzip's ``tell_compressed()`` counts bits. After the last read it is where
        the data ended: measured on rapidgzip 0.16's bzip2 decoder, it lands exactly on
        the end of the last stream that produced data. With ``parallelization=0``, as
        opened here, empty streams after that one are not counted: for a data stream
        and then ``bzip2 -c /dev/null``, it lands at the start of the empty stream.
        """
        tell = getattr(self._inner, "tell_compressed", None)
        if tell is None:
            return None
        try:
            bits = int(tell())
        except Exception:  # noqa: BLE001 - a position probe never breaks a read
            return None
        return -(-bits // 8)

    def block_offsets(self) -> dict[int, int] | None:
        """The decoder's index, compressed bit offset to decompressed offset, if it has one.

        ``block_offsets()`` forces the complete index, so this is for a caller that has
        read to the end. Measured on rapidgzip 0.16's bzip2 decoder, the keys are the
        start of each block's magic, the start of each end-of-stream marker of a stream
        with data, and the end of the last such marker.
        """
        offsets = getattr(self._inner, "block_offsets", None)
        if offsets is None:
            return None
        return dict(offsets())

    def read(self, n: int = -1, /) -> bytes:
        self._raise_if_lost()
        start = self._position()
        try:
            data = super().read(n)
        except Exception:
            self._after_failed_read(start)
            raise
        self._after_parked_fault()
        return data

    def readinto(self, b: WriteableBuffer, /) -> int:
        # Symmetry for a direct user of this class: behind _Bzip2EmptyStreamCheck, which
        # turns off readinto passthrough, every readinto from above arrives at read().
        self._raise_if_lost()
        start = self._position()
        try:
            n = super().readinto(b)
        except Exception:
            self._after_failed_read(start)
            raise
        self._after_parked_fault()
        return n

    def tell(self, /) -> int:
        self._raise_if_lost()
        return super().tell()

    def _position(self) -> int | None:
        try:
            return self._inner.tell()
        except Exception:  # noqa: BLE001 - only a rewind target; the read decides
            return None

    def _after_failed_read(self, start: int | None) -> None:
        """Move the decoder back to where a read that raised started, or give the stream up.

        The read returns nothing, but the decoder has moved past the chunks it decoded
        before the failure: measured on rapidgzip 0.16's bzip2 decoder, ``tell()`` read
        1 799 957 after a failed read that had delivered 1 048 576 bytes. Moving it back
        keeps ``tell()`` at the bytes the caller received, and a later read starts there
        rather than past bytes nobody returned.

        When the decoder cannot be moved back, its position matches nothing the caller
        received, and a later read would hand out bytes from past a gap with no error.
        So the stream is given up, as the rapidgzip child gives up in the same case:
        every later read, readinto, seek or ``tell()`` raises :class:`ReadError` with a
        message naming the cause. That happens when:

        - the read's own start was unknown, because the decoder's ``tell()`` raised;
        - the caller's source faulted during the read. A rewind would drive the decoder
          through that source again, and a fault the rewind parked would replace the one
          the read hit. After the first such fault the stream is given up, so no later
          call reaches the source;
        - the seek back raised, or parked a fault from the source. A parked ``Exception``
          is dropped so that the read's error is the one raised; an interrupt is not
          dropped: it is raised as itself, with the read's error as its context.

        Then the error is raised: the parked source fault if there is one, else the
        read's own.
        """
        trap = self._trap
        if trap is not None and trap.trapped is not None:
            pass  # _after_parked_fault below gives the stream up on the source's fault
        elif start is None:
            self._give_up(
                "a read failed, and the decoder's position before it was not known, so the "
                "decoder cannot be moved back"
            )
        else:
            rewind_error: Exception | None = None
            try:
                self._inner.seek(start)
            except Exception as exc:  # noqa: BLE001 - the read's own error is the one raised
                rewind_error = exc
            # A source fault the rewind parked is the cause worth naming, even when the
            # seek raised too: it is the one the caller can act on.
            if trap is not None and trap.trapped is not None:
                self._give_up_on_source_fault(trap.trapped)
                if isinstance(trap.trapped, Exception):
                    trap.trapped = None
            if rewind_error is not None:
                self._give_up(
                    f"a read failed, and moving the decoder back to where that read "
                    f"started failed too ({rewind_error!r})"
                )
        self._after_parked_fault()

    def _after_parked_fault(self) -> None:
        # A call that parked a source fault raises that fault. The decoder took the fault
        # for the end of its input and may have moved anywhere, so whatever the call
        # returned is never delivered and no later call can know the position: a read,
        # readinto or seek alike.
        if self._trap is not None and self._trap.trapped is not None:
            self._give_up_on_source_fault(self._trap.trapped)
        self._reraise_trapped()

    def _give_up_on_source_fault(self, fault: BaseException) -> None:
        self._give_up(
            f"a read from the stream's source failed ({fault!r}), and the decoder took "
            "that for the end of its input"
        )

    def _give_up(self, cause: str) -> None:
        if self._lost is None:
            self._lost = cause

    def _raise_if_lost(self) -> None:
        if self._lost is not None:
            raise ReadError(f"{self._lost}, so this stream cannot be read further")

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        self._raise_if_lost()
        try:
            result = super().seek(offset, whence)
        except Exception:
            self._after_parked_fault()
            raise
        self._after_parked_fault()
        return result

    def close(self) -> None:
        if self.closed:
            return
        # Trigger the finalize guard (closes the raw object) once; it is then disarmed.
        self._finalize()
        super().close()


class _TrappingSource(io.RawIOBase):
    """Source shim that never lets a Python-side fault cross into rapidgzip's C++ layer.

    rapidgzip aborts the whole process (SIGABRT, ``std::invalid_argument: Cannot convert
    nullptr Python object``) when a callback into its Python source raises mid-decode — e.g.
    the caller closed their own source underneath a live accelerator (``known-issues.md``
    Bug 3). No Python ``try/except`` around the accelerator can contain that abort. This shim
    wraps the source so every method **traps** rather than raises: it stores the first fault in
    ``trapped`` and returns a benign EOF-shaped result to rapidgzip; :class:`_AcceleratorStream`
    re-raises the stored fault after the accelerator call, turning the abort into a normal Python
    exception. It traps ``BaseException`` (not just ``Exception``): even a ``KeyboardInterrupt`` /
    ``SystemExit`` must never cross into C++, so a control-flow exception is **deferred** to the
    next accelerator boundary and re-raised there — never swallowed while the stream is open
    (:meth:`release` drops a fault still parked at close). It deliberately exposes
    **no** ``fileno`` so rapidgzip stays on its Python read path (a valid fileno would let it
    bypass this shim). Wraps only caller-owned sources; path sources open their own fd and are
    immune, so they are never trapped.
    """

    def __init__(self, inner: BinaryIO) -> None:
        super().__init__()
        self._inner = inner
        self.trapped: BaseException | None = None

    def release(self) -> None:
        """Drop the source, once the decoder that reads through this shim is closed.

        rapidgzip 0.16 never releases the Python file object it is given, closed or not
        (measured with a weak reference: ``IndexedBzip2File(f).close()`` leaves ``f``
        alive), so this shim outlives the stream. Without this, so would the source, and
        an ``io.BytesIO`` source with a full copy of its buffer: a fuzz run over the
        accelerated bzip2 path ran out of memory on it
        (``test_indexed_bzip2_frees_a_stream_source_after_close`` pins the fix). A call
        that still arrives reads an empty source.

        A fault still parked here goes too. Its traceback holds the frame of the
        source's own ``read``, whose ``self`` is the source, so keeping it would keep the
        source; and past the open, only a read-ahead nobody waited on parks one (see
        ``_AcceleratorStream._reraise_trapped``).
        """
        self._inner = io.BytesIO()
        self.trapped = None

    def _store(self, exc: BaseException) -> None:
        if self.trapped is None:
            # The caller's own exception: re-raised as itself, never translated.
            mark_callers_source(exc)
            self.trapped = exc

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        try:
            return bool(self._inner.seekable())
        except BaseException as exc:  # noqa: BLE001 - trap so no fault reaches the C++ layer
            self._store(exc)
            return False

    def read(self, size: int = -1, /) -> bytes:
        try:
            return self._inner.read(size)
        except BaseException as exc:  # noqa: BLE001 - trap; re-raised by _AcceleratorStream
            self._store(exc)
            return b""

    def readinto(self, buf: WriteableBuffer, /) -> int:
        mv = memoryview(buf).cast("B")
        try:
            data = self._inner.read(len(mv))
        except BaseException as exc:  # noqa: BLE001 - trap; re-raised by _AcceleratorStream
            self._store(exc)
            return 0
        mv[: len(data)] = data
        return len(data)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        try:
            return self._inner.seek(offset, whence)
        except BaseException as exc:  # noqa: BLE001 - trap; re-raised by _AcceleratorStream
            self._store(exc)
            return 0

    def tell(self) -> int:
        try:
            return self._inner.tell()
        except BaseException as exc:  # noqa: BLE001 - trap; re-raised by _AcceleratorStream
            self._store(exc)
            return 0


def _raise_parked(trap: _TrappingSource | None) -> None:
    """Raise the fault ``trap`` parked, if any, and clear it.

    Called inside an ``except`` block, the parked fault carries the accelerator's own
    error as its ``__context__``.
    """
    if trap is not None and trap.trapped is not None:
        exc = trap.trapped
        trap.trapped = None
        raise exc


def _open_accelerator(
    open_fn: Callable[..., object], source: CodecSource
) -> _AcceleratorStream:
    """Open ``source`` through an in-process rapidgzip decoder with the close-on-finalize
    guard and (for a caller-owned source) the Bug-3 trap.

    Only the bzip2 decoder runs in-process; gzip / zlib / deflate run in a child process
    (:func:`_open_rapidgzip`). A **path** source lets rapidgzip open its own fd — immune to
    Bug 3 — so it is passed straight through. A caller-owned stream is wrapped in a
    :class:`_TrappingSource` so a source-side fault becomes a re-raisable Python exception
    instead of a process abort. A fault parked while the decoder opens is raised here,
    before the stream is returned.
    """
    if isinstance(source, (str, os.PathLike)):
        return _AcceleratorStream(open_fn(source, parallelization=0))
    trap = _TrappingSource(source)
    try:
        raw = open_fn(trap, parallelization=0)
    except Exception:
        # As in _AcceleratorStream.read: the parked fault is the real cause of an
        # ordinary error, but never replaces an interrupt. No wrapper exists here to
        # release the trap; IndexedBzip2File has not been seen to raise at construction
        # (damaged, cut, empty and random inputs all raise on the first read instead).
        _raise_parked(trap)
        raise
    stream = _AcceleratorStream(raw, trap=trap)
    try:
        _raise_parked(trap)
    except BaseException:
        stream.close()
        raise
    return stream
