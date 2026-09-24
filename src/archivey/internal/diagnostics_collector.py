"""Lifecycle diagnostic collector and emission path.

Owns exact counts, bounded retention (aggregate + member attachments), watermark-based
ranged snapshots, policy resolution, logging/callback delivery, and RAISE escalation.

Memory is bounded by design: the only lifetime-retained structure is ``_retained``, which
is capped at ``max_retained``. Exact counts are kept as a small fixed per-code ``Counter``,
and ranged/stream snapshots are computed by differencing a cheap :class:`DiagnosticWatermark`
(a sequence number plus a per-code count snapshot) against the live counters — so nothing
grows with the number of emitted diagnostics or opened streams.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from archivey.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    DiagnosticContext,
    DiagnosticDisposition,
    DiagnosticPolicy,
    DiagnosticSeverity,
    DiagnosticSummary,
    OnDiagnostic,
    validate_code_context,
)
from archivey.exceptions import DiagnosticRaisedError, UnsupportedOperationError

if TYPE_CHECKING:
    from archivey.config import ArchiveyConfig
    from archivey.types import ArchiveMember

_log = logging.getLogger("archivey.diagnostics")


@dataclass(frozen=True)
class DiagnosticWatermark:
    """Opaque collector position: the sequence number plus per-code counts at capture time.

    Creating one copies no diagnostics and costs no retention slots. Ranged snapshots
    difference two watermarks (or a watermark against "now"), so the collector needs no
    per-emission log to answer "what happened since here".
    """

    _sequence: int
    _total: int
    _counts: Mapping[DiagnosticCode, int]


@dataclass
class _RetainedEntry:
    sequence: int
    diagnostic: Diagnostic


@dataclass
class _LoggedEmit:
    diagnostic: Diagnostic
    attached: bool
    raised: BaseException | None = None


@dataclass
class EmitLog:
    """The emits one piece of repeatable work made, in order, for ``DiagnosticCollector.
    replaying`` to record the first time and replay after."""

    entries: list[_LoggedEmit] = field(default_factory=list)


@dataclass
class _Replay:
    log: EmitLog
    cursor: int = 0


class DiagnosticCollector:
    """One collector per detection / reader / top-level extract / standalone stream."""

    def __init__(
        self,
        *,
        policy: DiagnosticPolicy | None = None,
        max_retained: int = 256,
        on_diagnostic: OnDiagnostic | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if max_retained < 0:
            raise ValueError("max_retained_diagnostic_references must be >= 0")
        self._policy = policy if policy is not None else DiagnosticPolicy()
        self._max_retained = max_retained
        self._on_diagnostic = on_diagnostic
        self._logger = logger if logger is not None else _log
        self._lock = threading.RLock()
        self._sequence = 0
        self._total_count = 0
        self._counts: Counter[DiagnosticCode] = Counter()
        self._retained: list[_RetainedEntry] = []
        self._slots_used = 0
        # Thread ids currently inside a delivery (log/callback) step. Keyed per thread so
        # legitimate concurrent emits on separate threads do not read as reentrancy, while
        # a callback re-entering emit on its own thread still trips the guard.
        self._emitting_threads: set[int] = set()
        # Per thread, the log a ``replaying()`` block records into or replays from. Per
        # thread, so another thread's stream on the same reader is never replayed.
        self._replays: dict[int, _Replay] = {}

    @contextmanager
    def replaying(self, log: EmitLog) -> Iterator[None]:
        """Record this thread's emits into ``log``, or replay the ones it already holds.

        For work a reader may have to repeat, such as a random-access member walk
        started over after a failure: run each piece inside ``replaying`` with its own
        log. The first run records every emit. A repeat takes its emits from the log in
        order: nothing is counted, retained, logged or called back again, a recorded
        attachment is made to the member the repeat passes, and an emit that raised the
        first time raises the same exception again, so the repeat takes the path the
        first run took. Emits past the end of the log are new; they go through and are
        recorded. An emit whose code differs from the recorded one means the work is not
        repeating itself, and replay stops there.
        """
        thread_id = threading.get_ident()
        with self._lock:
            self._replays[thread_id] = _Replay(log)
        try:
            yield
        finally:
            with self._lock:
                self._replays.pop(thread_id, None)

    @property
    def policy(self) -> DiagnosticPolicy:
        return self._policy

    @property
    def max_retained(self) -> int:
        return self._max_retained

    def watermark(self) -> DiagnosticWatermark:
        with self._lock:
            return DiagnosticWatermark(
                _sequence=self._sequence,
                _total=self._total_count,
                _counts=dict(self._counts),
            )

    def _summary_between(
        self,
        start_seq: int,
        start_total: int,
        start_counts: Mapping[DiagnosticCode, int],
        end_seq: int,
        end_total: int,
        end_counts: Mapping[DiagnosticCode, int],
    ) -> DiagnosticSummary:
        """Build a summary for the half-open range ``(start_seq, end_seq]``.

        Counts and totals are exact deltas of the two count snapshots; retained detail is
        filtered from the (bounded) retention list by sequence. The caller holds the lock.
        """
        total = end_total - start_total
        count_map: dict[DiagnosticCode, int] = {}
        for code, n in end_counts.items():
            delta = n - start_counts.get(code, 0)
            if delta:
                count_map[code] = delta
        retained = tuple(
            e.diagnostic for e in self._retained if start_seq < e.sequence <= end_seq
        )
        return DiagnosticSummary(
            total_count=total,
            counts=count_map,
            retained=retained,
            dropped_count=total - len(retained),
        )

    def snapshot(
        self, *, since: DiagnosticWatermark | None = None
    ) -> DiagnosticSummary:
        """Fresh immutable cumulative snapshot, or the delta since ``since``."""
        with self._lock:
            if since is None:
                return self._summary_between(
                    0, 0, {}, self._sequence, self._total_count, self._counts
                )
            return self._summary_between(
                since._sequence,
                since._total,
                since._counts,
                self._sequence,
                self._total_count,
                self._counts,
            )

    def emit(
        self,
        *,
        code: DiagnosticCode,
        message: str,
        context: DiagnosticContext,
        severity: DiagnosticSeverity = DiagnosticSeverity.WARNING,
        member: ArchiveMember | None = None,
        attach_to_member: bool = False,
        logger: logging.Logger | None = None,
        escalate_as: type[BaseException] | None = None,
        escalate_message: str | None = None,
        escalate_kwargs: dict[str, object] | None = None,
    ) -> Diagnostic:
        """Emit one diagnostic through the ordered policy matrix.

        Always increments exact counts. Under IGNORE, skips retention/log/callback.
        Under COLLECT/RAISE, retains (budget permitting), logs, and callbacks.
        Under RAISE (or when ``escalate_as`` is set), raises after delivery.

        ``escalate_as`` (e.g. :class:`~archivey.exceptions.CorruptionError` for a
        rejected TAR header) takes precedence over :class:`DiagnosticRaisedError` for the
        terminal exception after the delivery steps that ran for the disposition.

        It also raises **whatever the disposition is, ``IGNORE`` included**: it marks a
        condition the caller's policy cannot suppress, not merely a choice of exception
        type. Under ``IGNORE`` that means an exception with no log line and no callback
        behind it, only the count. A caller that wants the policy to decide should pass
        ``escalate_as`` only when the disposition is already ``RAISE``.
        """
        validate_code_context(code, context)
        disposition = self._policy.resolve(code)
        log = logger if logger is not None else self._logger
        thread_id = threading.get_ident()

        with self._lock:
            replay = self._replays.get(thread_id)
            if replay is not None and replay.cursor < len(replay.log.entries):
                logged = replay.log.entries[replay.cursor]
                if logged.diagnostic.code is code:
                    replay.cursor += 1
                    if (
                        logged.attached
                        and member is not None
                        and logged.diagnostic not in member._diagnostics
                    ):
                        _attach_diagnostic(member, logged.diagnostic)
                    if logged.raised is not None:
                        raise logged.raised
                    return logged.diagnostic
                replay.cursor = len(replay.log.entries)
            if thread_id in self._emitting_threads:
                raise UnsupportedOperationError(
                    "Diagnostic callback/reentrancy: cannot drive another operation on "
                    "the same reader/stream while a diagnostic is being emitted."
                )
            self._sequence += 1
            sequence = self._sequence
            diagnostic = Diagnostic(
                occurrence_id=uuid.uuid4().hex,
                code=code,
                severity=severity,
                message=message,
                context=context,
            )
            self._total_count += 1
            self._counts[code] += 1

            retained_aggregate = False
            attached = False
            if disposition is not DiagnosticDisposition.IGNORE:
                if self._slots_used < self._max_retained:
                    self._retained.append(
                        _RetainedEntry(sequence=sequence, diagnostic=diagnostic)
                    )
                    self._slots_used += 1
                    retained_aggregate = True
                if (
                    retained_aggregate
                    and attach_to_member
                    and member is not None
                    and self._slots_used < self._max_retained
                ):
                    _attach_diagnostic(member, diagnostic)
                    self._slots_used += 1
                    attached = True
            logged_emit: _LoggedEmit | None = None
            if replay is not None:
                logged_emit = _LoggedEmit(diagnostic, attached)
                replay.log.entries.append(logged_emit)
                replay.cursor = len(replay.log.entries)

            should_deliver = disposition is not DiagnosticDisposition.IGNORE
            should_raise_diagnostic = disposition is DiagnosticDisposition.RAISE
            if should_deliver:
                self._emitting_threads.add(thread_id)

        try:
            if should_deliver:
                log.warning("%s", message)
                if self._on_diagnostic is not None:
                    self._on_diagnostic(diagnostic)
            # Only the raise this emit makes is part of what a replay repeats; one out of
            # the logger or the callback, a KeyboardInterrupt included, is not.
            raised: BaseException | None = None
            if escalate_as is not None:
                msg = escalate_message if escalate_message is not None else message
                kwargs = escalate_kwargs if escalate_kwargs is not None else {}
                raised = escalate_as(msg, **kwargs)
            elif should_raise_diagnostic:
                raised = DiagnosticRaisedError(message, diagnostic=diagnostic)
            if raised is not None:
                if logged_emit is not None:
                    logged_emit.raised = raised
                raise raised
        finally:
            with self._lock:
                self._emitting_threads.discard(thread_id)

        return diagnostic

    def escalate_only(
        self,
        *,
        code: DiagnosticCode,
        message: str,
        context: DiagnosticContext,
        severity: DiagnosticSeverity = DiagnosticSeverity.WARNING,
    ) -> None:
        """Evaluate ``code``'s policy and raise on ``RAISE`` — recording nothing.

        For a diagnostic that is *recorded* at most once per stream but whose policy must
        be honoured on every occurrence. Deduplication keeps the report bounded and
        readable; escalation is control flow for a caller who explicitly asked to be
        stopped, and a guard that disarms after firing once is not a guard.

        This is the rule for **every** once-per-stream code (see ``diagnostics``), not a
        per-code exception. The dedup bookkeeping stays with the emitter, which is what
        knows the scope; this collector only knows the policy.

        Nothing is counted, retained, logged or called back, so a caller reading
        ``reader.diagnostics`` sees exactly the one record the first occurrence left. The
        raised error still carries a full :class:`Diagnostic` value describing *this*
        occurrence — the caller being stopped should see the seek that stopped them, not
        the first one.
        """
        validate_code_context(code, context)
        if self._policy.resolve(code) is not DiagnosticDisposition.RAISE:
            return
        raise DiagnosticRaisedError(
            message,
            diagnostic=Diagnostic(
                occurrence_id=uuid.uuid4().hex,
                code=code,
                severity=severity,
                message=message,
                context=context,
            ),
        )


def _attach_diagnostic(member: ArchiveMember, diagnostic: Diagnostic) -> None:
    """Append a diagnostic to a member's attached tuple (library retention slot)."""
    current = member._diagnostics
    object.__setattr__(member, "_diagnostics", current + (diagnostic,))


def collector_from_config(config: ArchiveyConfig) -> DiagnosticCollector:
    """Build a collector from an :class:`~archivey.config.ArchiveyConfig`."""
    return DiagnosticCollector(
        policy=config.diagnostic_policy,
        max_retained=config.max_retained_diagnostic_references,
        on_diagnostic=config.on_diagnostic,
    )


def resolve_collector(collector: DiagnosticCollector | None) -> DiagnosticCollector:
    """Return ``collector``, or a throwaway COLLECT-default collector from library defaults.

    Used at emission sites that may not yet have a reader/stream-owned collector threaded
    through (standalone codec streams). Prefer passing the shared collector when available.
    """
    if collector is not None:
        return collector
    from archivey.config import DEFAULT_ARCHIVEY_CONFIG

    return collector_from_config(DEFAULT_ARCHIVEY_CONFIG)


__all__ = [
    "DiagnosticCollector",
    "DiagnosticWatermark",
    "collector_from_config",
    "resolve_collector",
]
