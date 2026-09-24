"""Whether a reader call is allowed right now, and who cleans up afterwards.

This module holds no archive data and does no I/O. ``BaseArchiveReader`` asks it two
kinds of question: "may this call start?" (and, if not, which usage error to raise, or
whether to wait) and "am I the one who must close the archive now?". Every method is a
transition on, or a test of, four independent pieces of state:

- **Who owns the reader.** ``_root`` is the reader-wide pass in progress (``members()``
  on a default reader, ``stream_members()``, ``extract_all()``), ``_children`` are
  library-internal scopes under it (link reads, the extraction coordinator's own
  opens), and ``_workers`` are short ``open()`` / ``read()`` / ``get()`` calls (and, on a
  ``CONCURRENT`` reader, ``members()`` / ``members_report()``). Each is
  an :class:`OperationToken` that records the thread that took it, so a callback that
  re-enters the reader on that thread gets a message about the callback, not about a
  second caller.
- **Whether the member list exists yet** (``cache_state``). See :class:`ReaderState`.
- **How many member streams are alive** (``_live_streams``, plus ``_reservations`` for
  an ``open()`` that has passed the gate but not built its stream yet).
- **How far the close has got** (``lifecycle`` and the leases), below.

Leases, and why teardown is separate from close
-----------------------------------------------

Marking the reader closed does not close the archive. A lease is held by whatever still
reads through the source: the reader itself (one lease, from construction until
``close()``, kept as ``_reader_lease_held``) plus each live member stream or reservation
(counted in ``_lease_count``). The underlying file handle or ``unrar`` /
``7z`` process is torn down by whoever drops the **last** lease. ``close()`` drops the
reader's lease first and then closes the still-open member streams, so with streams open
the last lease usually goes with the last stream's close, not with the reader's. That is
why :meth:`ReaderState.mark_reader_closed` and :meth:`ReaderState.release_live_stream`
both return "run teardown now", and why :meth:`ReaderState.claim_teardown` makes sure
only one caller acts on it. Teardown always follows the streams, never runs under one::

    OPEN --mark_reader_closed()--> READER_CLOSED --claim_teardown()--> TEARDOWN_RUNNING
         (drops the reader's lease)                 (lease count 0; succeeds once)
    TEARDOWN_RUNNING --complete_teardown()--> TEARDOWN_COMPLETE

``_closing`` is set once a ``close()`` has passed its checks, and stays set while it drains
in-flight workers (a wait that only happens under ``CONCURRENT``; a default reader has
refused the close instead). New calls are refused from then on, and other closers wait
for the transition.
"""

from __future__ import annotations

import enum
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from archivey.exceptions import ArchiveyUsageError, ConcurrentAccessError
from archivey.types import MemberStreams

if TYPE_CHECKING:
    from archivey.internal.open_site import OpenSite
    from archivey.internal.streams.archive_stream import ArchiveStream


class CacheState(enum.Enum):
    UNMATERIALIZED = "unmaterialized"
    MATERIALIZING = "materializing"
    MATERIALIZED = "materialized"


class LifecycleState(enum.Enum):
    OPEN = "open"
    READER_CLOSED = "reader_closed"
    TEARDOWN_RUNNING = "teardown_running"
    TEARDOWN_COMPLETE = "teardown_complete"


@dataclass(eq=False)
class LiveStreamReservation:
    """Opaque live-stream slot reservation.

    Disjoint from ``id()`` values in ``_live_streams``. A reservation must never
    be stored in that set: ``release_live_stream_id`` reasons about id reuse, and
    a synthetic int dropped in there would be mistakable for a stream.
    """


@dataclass(eq=False)
class OperationToken:
    """Unforgeable root, child, or short-lived worker operation-owner token."""

    name: str
    parent: OperationToken | None = None
    kind: str = "root"  # "root" | "child" | "worker"
    # The thread that acquired the token. Used to detect same-thread re-entry (a
    # diagnostic/progress callback driving the reader that is mid-operation on this very
    # thread), which must raise a usage error instead of deadlocking on a wait for itself.
    # The Thread object, not ``get_ident()``: idents are reused once a thread exits, and
    # a token can outlive its thread (a generator holding a pass is never closed), which
    # would misdiagnose a fresh thread with the same ident as re-entering.
    # ``_materializing_thread`` and ``_internal_open_threads`` stay idents: both are
    # cleared in ``finally`` on the thread that set them, so neither outlives it.
    thread: threading.Thread = field(
        default_factory=threading.current_thread, repr=False
    )
    # True while the generator holding this pass (``stream_members()``, streaming
    # ``__iter__``) is suspended at a yield. Its thread is then running the caller's
    # loop body, so a call from that thread is not re-entry from a callback and
    # ``_same_thread_token_locked`` skips the token. While the generator is executing a
    # step (where a diagnostic callback fires) the flag is False and the token counts.
    # Set with :meth:`ReaderState.set_suspended`.
    suspended: bool = field(default=False, repr=False)
    _released: bool = field(default=False, repr=False)


class ReaderState:
    """Per-reader concurrency and lifecycle bookkeeping (see the module docstring).

    **Materialization** means building the member list, once. A reader does not list the
    archive when it is opened. The first call that needs members (``members()``,
    ``open()``, ``get()``) walks the whole archive and publishes one immutable snapshot,
    which every later call reads. :meth:`begin_materialization` elects the caller that
    does the walk: it returns ``True`` to that caller and ``False`` to one for whom the
    snapshot already exists. The three :class:`CacheState` values are before, during and
    after that walk.

    **The default path is the strict one.** Without ``MemberStreams.CONCURRENT``
    (``concurrent_members=True``) this class mostly refuses: one live member stream, one
    reader-wide operation, no overlapping worker calls, and no shared-handle locks
    needed. ``CONCURRENT`` turns those refusals into waits: several live streams,
    a second materializer waits for the first one's snapshot, and ``close()`` drains
    in-flight worker calls. Most methods therefore have two behaviours, chosen by
    :attr:`concurrent`. Distinct reader-wide passes and access to one stream stay
    single-owner (caller-synchronized) either way.
    """

    def __init__(
        self,
        *,
        member_streams: MemberStreams,
        open_site: OpenSite | None,
    ) -> None:
        self.member_streams = member_streams
        self.open_site = open_site
        self._lock = threading.RLock()
        self._materialization_cv = threading.Condition(self._lock)
        self._workers_cv = threading.Condition(self._lock)
        self._close_cv = threading.Condition(self._lock)
        self.cache_state = CacheState.UNMATERIALIZED
        self.lifecycle = LifecycleState.OPEN
        self._closing = False
        self._root: OperationToken | None = None
        self._children: set[OperationToken] = set()
        self._workers: set[OperationToken] = set()
        self._live_streams: set[int] = set()
        self._reservations: set[LiveStreamReservation] = set()
        # Leases of live streams and reservations only.
        self._lease_count = 0
        # The reader's own lease, held until close. Read by _outstanding_leases_locked
        # rather than folded into ``_lease_count``, so dropping it is one store: there
        # is no second write for an interrupt to separate it from, and a close
        # interrupted before that store is finished by the next mark_reader_closed().
        self._reader_lease_held = True
        self._teardown_claimed = False
        self._stream_shutdown_claimed = False
        # Library-internal open windows (extract_all's coordinator, first-touch link
        # reads), keyed BY THREAD: the exemption from the live-stream gate and from
        # worker rejection applies only to the thread that entered the window. A plain
        # reader-wide depth counter would silently admit a *foreign* thread's open()
        # during extract_all on a non-CONCURRENT reader — and on backends without a
        # shared-handle lock the interleaved seek+read pairs then serve wrong member
        # bytes instead of the loud error this gate exists to raise (deep review N3).
        self._internal_open_threads: dict[int, int] = {}
        # The thread that currently owns the materialization election, for same-thread
        # re-entry detection (deep review N4a).
        self._materializing_thread: int | None = None

    @property
    def concurrent(self) -> bool:
        return MemberStreams.CONCURRENT in self.member_streams

    @property
    def seekable(self) -> bool:
        return MemberStreams.SEEKABLE in self.member_streams

    def require_open(self, op: str) -> None:
        with self._lock:
            self._require_admissible_locked(op)

    def current_root(self) -> OperationToken | None:
        with self._lock:
            return self._root

    def begin_internal_opens(self) -> None:
        tid = threading.get_ident()
        with self._lock:
            self._internal_open_threads[tid] = (
                self._internal_open_threads.get(tid, 0) + 1
            )

    def end_internal_opens(self) -> None:
        tid = threading.get_ident()
        with self._lock:
            depth = self._internal_open_threads.get(tid, 0) - 1
            if depth > 0:
                self._internal_open_threads[tid] = depth
            else:
                self._internal_open_threads.pop(tid, None)

    def _internal_opens_active_locked(self) -> bool:
        """Whether the CURRENT thread is inside a library-internal open window."""
        return self._internal_open_threads.get(threading.get_ident(), 0) > 0

    def acquire_pass(self, name: str) -> OperationToken:
        """Acquire a data-pass token: root when free, else a child under an internal owner."""
        with self._lock:
            self._require_admissible_locked(name)
            internal = self._internal_opens_active_locked()
            if self._root is not None and internal:
                child = OperationToken(name=name, parent=self._root, kind="child")
                self._children.add(child)
                return child
            if not internal:
                self._reject_same_thread_reentry_locked(f"start {name!r}")
            if self._root is not None:
                raise ArchiveyUsageError(
                    f"Cannot start {name!r}: another reader operation "
                    f"({self._root.name!r}) is already active."
                )
            if self._workers:
                raise ArchiveyUsageError(
                    f"Cannot start {name!r}: a concurrent open()/read() call is still "
                    "in progress."
                )
            token = OperationToken(name=name, kind="root")
            self._root = token
            return token

    def set_suspended(self, token: OperationToken, suspended: bool) -> None:
        """Mark a generator-held pass as suspended at a yield, or running again."""
        with self._lock:
            token.suspended = suspended

    def release_pass(self, token: OperationToken) -> None:
        with self._lock:
            if token._released:
                return
            token._released = True
            if token.parent is not None or token.kind == "child":
                self._children.discard(token)
                return
            if self._root is token:
                self._root = None
            self._children.clear()

    def enter_child(self, root: OperationToken, name: str) -> OperationToken:
        with self._lock:
            if root._released or self._root is not root:
                raise ArchiveyUsageError(
                    f"Cannot enter child scope {name!r}: root operation is not active."
                )
            child = OperationToken(name=name, parent=root, kind="child")
            self._children.add(child)
            return child

    def release_child(self, child: OperationToken) -> None:
        with self._lock:
            if child._released:
                return
            child._released = True
            self._children.discard(child)

    def acquire_worker(self, name: str) -> OperationToken:
        """Short-lived token for random ``open()`` / ``read()`` (D7).

        Rejected while a reader-wide root pass is active (unless under library-internal
        opens). Idle open streams (leases only) do not block workers. Stream I/O after
        this token is released does not re-check the gate.
        """
        with self._lock:
            self._require_admissible_locked(name)
            internal = self._internal_opens_active_locked()
            if not internal:
                self._reject_same_thread_reentry_locked(f"call {name!r}")
            if self._root is not None and not internal:
                raise ArchiveyUsageError(
                    f"Cannot call {name!r}: another reader operation "
                    f"({self._root.name!r}) is already active."
                )
            # Under an internal library owner (extract_all), the OWNING THREAD's worker
            # opens are admitted as children of that root; other threads are rejected
            # above like during any root pass.
            if self._root is not None and internal:
                token = OperationToken(name=name, parent=self._root, kind="child")
                self._children.add(token)
                return token
            token = OperationToken(name=name, kind="worker")
            self._workers.add(token)
            return token

    def release_worker(self, token: OperationToken) -> None:
        with self._lock:
            if token._released:
                return
            token._released = True
            if token.kind == "child" or token.parent is not None:
                self._children.discard(token)
                return
            self._workers.discard(token)
            if not self._workers:
                self._workers_cv.notify_all()

    def begin_materialization(self) -> bool:
        """Elect a materialization owner or wait for a published snapshot.

        Returns ``True`` if the caller must perform materialization (and later call
        :meth:`complete_materialization` or :meth:`fail_materialization`). Returns
        ``False`` if the immutable snapshot is already published (including after
        waiting under ``CONCURRENT``).

        Under ``MemberStreams.CONCURRENT``, a second caller that overlaps an in-progress
        materialization blocks on a condition until the snapshot is published or the
        attempt fails (then re-elects). Without ``CONCURRENT``, overlapping materialization
        still raises :class:`~archivey.exceptions.ArchiveyUsageError`. Uncontended paths
        take the elect-and-run branch with no wait.
        """
        with self._lock:
            while True:
                self._require_admissible_locked("materialization")
                if self.cache_state is CacheState.MATERIALIZED:
                    return False
                if self.cache_state is CacheState.MATERIALIZING:
                    if self._materializing_thread == threading.get_ident():
                        # Same-thread re-entry: a diagnostic/progress callback fired
                        # during materialization called back into the reader. Waiting
                        # would deadlock this thread on a notify only it can send
                        # (deep review N4a); a non-CONCURRENT reader would raise the
                        # generic message below, which misdiagnoses the situation.
                        raise ArchiveyUsageError(
                            "Reader re-entered while it is materializing its member "
                            "list on this same thread (e.g. from a diagnostic or "
                            "progress callback). Callbacks must not call back into "
                            "the reader that triggered them."
                        )
                    if not self.concurrent:
                        raise ArchiveyUsageError(
                            "Cannot start materialization: another materialization is "
                            "already in progress."
                        )
                    self._materialization_cv.wait()
                    continue
                # UNMATERIALIZED — elect this caller.
                self.cache_state = CacheState.MATERIALIZING
                self._materializing_thread = threading.get_ident()
                return True

    def complete_materialization(self) -> None:
        with self._lock:
            self.cache_state = CacheState.MATERIALIZED
            self._materializing_thread = None
            self._materialization_cv.notify_all()

    def fail_materialization(self) -> None:
        with self._lock:
            if self.cache_state is CacheState.MATERIALIZING:
                self.cache_state = CacheState.UNMATERIALIZED
                self._materializing_thread = None
                self._materialization_cv.notify_all()

    def reserve_live_stream(self) -> LiveStreamReservation:
        """Take a live-stream lease before the stream object exists.

        Raises :class:`~archivey.exceptions.ConcurrentAccessError` under the same
        rules as :meth:`acquire_live_stream` (including the concurrent / internal-open
        bypass). The lease is held until :meth:`bind_live_stream` or
        :meth:`release_reservation`.
        """
        with self._lock:
            self._require_lifecycle_open_locked("open()")
            if not (self.concurrent or self._internal_opens_active_locked()):
                if self._live_streams or self._reservations:
                    site = self.open_site
                    loc = site.location if site is not None else "<unknown>"
                    raise ConcurrentAccessError(
                        "A member stream is already open on this reader. Close it "
                        "before opening another, or reopen the archive with "
                        "concurrent_members=True "
                        f"(this archive was opened without concurrent_members at {loc})."
                    )
            token = LiveStreamReservation()
            self._reservations.add(token)
            self._lease_count += 1
            return token

    def bind_live_stream(
        self, token: LiveStreamReservation, stream: ArchiveStream
    ) -> None:
        """Consume ``token``, recording ``id(stream)`` as the live stream.

        Infallible: no lifecycle re-check. ``open()`` still holds a worker across
        reserve → open → bind, so ``close()`` cannot complete in between. Does not
        increment the lease — the reservation already holds it.
        """
        with self._lock:
            self._reservations.discard(token)
            self._live_streams.add(id(stream))

    def release_reservation(self, token: LiveStreamReservation) -> bool:
        """Drop an unbound reservation. True → the caller should run teardown.

        Idempotent: a token already bound or released is a no-op.
        """
        with self._lock:
            if token not in self._reservations:
                return False
            self._reservations.discard(token)
            return self._release_lease_locked()

    def acquire_live_stream(self, stream: ArchiveStream) -> None:
        """Register a public member stream; enforce the single-live-stream gate.

        Implemented as reserve-then-bind so the eager ``open()`` path and the lazy
        ``stream_members`` path share one gate check.
        """
        token = self.reserve_live_stream()
        self.bind_live_stream(token, stream)

    def release_live_stream(self, stream: ArchiveStream) -> bool:
        """Release a stream lease. Returns True if the caller should run teardown."""
        return self.release_live_stream_id(id(stream))

    def release_live_stream_id(self, sid: int) -> bool:
        """Release a stream lease by identity token.

        Separate from :meth:`release_live_stream` so a stream's own finalizer can hold
        the token instead of the stream — holding the stream would keep it alive and
        stop the finalizer from ever running. Safe against id reuse: the token is
        removed when the stream closes, and a finalizer runs before its referent's
        memory can be handed to another object.
        """
        with self._lock:
            if sid not in self._live_streams:
                return False
            self._live_streams.discard(sid)
            return self._release_lease_locked()

    def mark_reader_closed(self) -> bool:
        """Mark reader closed and release the reader lease. True → run teardown now.

        Under ``MemberStreams.CONCURRENT``, blocks until in-flight worker ``open()`` /
        ``read()`` / ``get()`` / ``members()`` calls return, then transitions to closed.
        Escaped idle member streams keep their lifecycle leases and do not block close.
        A worker that never returns is a caller bug (same as any lock); there is no
        artificial timeout.

        Without ``CONCURRENT``, overlapping worker calls still raise
        :class:`~archivey.exceptions.ArchiveyUsageError`. Concurrent double-``close()`` is
        idempotent: one thread drains and closes; others wait for that transition and
        return without running teardown again.
        """
        with self._lock:
            while True:
                # First, before any wait: a thread closing from INSIDE one of its own
                # calls (a diagnostic/progress callback calling close()) would wait
                # forever for a call that cannot return until close() does (deep
                # review N4b), and the generic messages below would send the user
                # looking for a second caller that does not exist (S21-K4).
                token = self._same_thread_token_locked()
                if token is not None:
                    raise ArchiveyUsageError(
                        "Cannot close the archive reader from inside one of its own "
                        f"calls ({token.name!r}; e.g. from a diagnostic or progress "
                        "callback). Callbacks must not close the reader that "
                        "triggered them."
                    )
                # Another closer is draining, or close already finished.
                if self._closing or self.lifecycle is not LifecycleState.OPEN:
                    while self.lifecycle is LifecycleState.OPEN and self._closing:
                        self._close_cv.wait()
                    if self.lifecycle is LifecycleState.READER_CLOSED:
                        # A closer interrupted between the transition and its lease
                        # drop left the reader's lease counted. Finish the drop here.
                        return self._drop_reader_lease_locked()
                    if self.lifecycle is LifecycleState.OPEN:
                        # The draining closer was interrupted (its except arm below
                        # reset ``_closing``), so nothing closed the reader. Returning
                        # False would tell close() a peer had, and it would then mark
                        # itself closed with teardown never run. Retry as the closer.
                        # Invariant: False is returned only when lifecycle is not OPEN.
                        continue
                    return False
                if self._root is not None:
                    raise ArchiveyUsageError(
                        "Cannot close the archive reader while another reader "
                        f"operation ({self._root.name!r}) is active."
                    )
                if self._workers and not self.concurrent:
                    raise ArchiveyUsageError(
                        "Cannot close the archive reader while an open()/read() call "
                        "is still in progress."
                    )
                self._closing = True
                try:
                    while self._workers:
                        self._workers_cv.wait()
                    # Notify last. If an interrupt lands after the transition but
                    # before the lease drop, the retry branch above finishes the drop.
                    self.lifecycle = LifecycleState.READER_CLOSED
                    run_teardown = self._drop_reader_lease_locked()
                    self._close_cv.notify_all()
                    return run_teardown
                except BaseException:
                    self._closing = False
                    self._close_cv.notify_all()
                    raise

    def claim_stream_shutdown(self) -> bool:
        """True exactly once, for the caller that should close live member streams.

        The same shape as :meth:`claim_teardown`, and needed for the same reason.
        ``mark_reader_closed`` returns ``False`` both for "I performed the transition
        but leases remain" and for "a peer had already closed", so its return value
        cannot identify the thread that owns stream shutdown. Without this guard two
        concurrent ``close()`` calls both walk the stream registry — and
        ``ArchiveStream.close`` tests ``self.closed`` outside its lock, so both can
        reach ``inner.close()`` on the same stream. A backend that is not re-entrant on
        close (rapidgzip especially) would be entered twice.

        Like teardown, the claim is not retried: a ``close()`` whose stream shutdown
        raises has already consumed it.
        """
        with self._lock:
            if self._stream_shutdown_claimed:
                return False
            self._stream_shutdown_claimed = True
            return True

    def claim_teardown(self) -> bool:
        with self._lock:
            if self._teardown_claimed:
                return False
            # ``_teardown_claimed`` is set one line before the lifecycle moves to
            # TEARDOWN_RUNNING and is never reset, so READER_CLOSED is the only state
            # a claim can succeed from.
            if self.lifecycle is not LifecycleState.READER_CLOSED:
                return False
            if self._outstanding_leases_locked() > 0:
                return False
            self._teardown_claimed = True
            self.lifecycle = LifecycleState.TEARDOWN_RUNNING
            return True

    def complete_teardown(self) -> None:
        """Mark teardown finished. A teardown failure is the caller's to propagate
        (``_maybe_teardown`` raises it, alone or in an ``ExceptionGroup``); the state
        machine keeps no copy — a stored-but-never-surfaced error would be a silent
        swallow path."""
        with self._lock:
            self.lifecycle = LifecycleState.TEARDOWN_COMPLETE

    def _drop_reader_lease_locked(self) -> bool:
        """Drop the reader's own lease, once. True → the caller should run teardown."""
        if not self._reader_lease_held:
            return False
        self._reader_lease_held = False
        return self._teardown_due_locked()

    def _release_lease_locked(self) -> bool:
        """Drop one stream or reservation lease. True → the caller should run teardown."""
        if self._lease_count > 0:
            self._lease_count -= 1
        return self._teardown_due_locked()

    def _outstanding_leases_locked(self) -> int:
        return self._lease_count + (1 if self._reader_lease_held else 0)

    def _teardown_due_locked(self) -> bool:
        return (
            self.lifecycle is LifecycleState.READER_CLOSED
            and self._outstanding_leases_locked() == 0
            and not self._teardown_claimed
        )

    def _same_thread_token_locked(self) -> OperationToken | None:
        """A token this thread holds while inside a reader call, if any.

        Any hit means the current call is re-entry from inside one of the reader's own
        calls on this thread: in practice a diagnostic or progress callback. A pass whose
        generator is suspended at a yield is skipped (see
        :attr:`OperationToken.suspended`).
        """
        current = threading.current_thread()
        root = self._root
        tokens = (*self._children, *self._workers)
        for token in (root, *tokens) if root is not None else tokens:
            if token.thread is current and not token.suspended:
                return token
        return None

    def _reject_same_thread_reentry_locked(self, action: str) -> None:
        """Raise the re-entry usage error before the generic overlap messages.

        Without this, a non-``CONCURRENT`` reader answered a callback's re-entry with
        "another reader operation is already active", which points at a second caller
        that does not exist (S21-K4).
        """
        token = self._same_thread_token_locked()
        if token is not None:
            raise ArchiveyUsageError(
                f"Cannot {action}: the reader was re-entered from inside its own "
                f"{token.name!r} call on this same thread (e.g. from a diagnostic or "
                "progress callback). Callbacks must not call back into the reader "
                "that triggered them."
            )

    def _require_lifecycle_open_locked(self, op: str) -> None:
        """Lifecycle is still OPEN (including during draining close)."""
        if self.lifecycle is not LifecycleState.OPEN:
            raise ArchiveyUsageError(
                f"{op} is not available after the archive reader has been closed."
            )

    def _require_admissible_locked(self, op: str) -> None:
        """Reject new public admissions after close started or finished."""
        if self.lifecycle is not LifecycleState.OPEN or self._closing:
            raise ArchiveyUsageError(
                f"{op} is not available after the archive reader has been closed."
            )
