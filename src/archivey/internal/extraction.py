"""The extraction coordinator and decompression-bomb tracker.

``ExtractionCoordinator`` is a **pull-based sink**: it drives the reader
(``members_report_if_available()``, ``_iter_with_data()``, ``compressed_source_size``) and
selects an algorithm, rather than a push-model helper that buffers deferred link state.
Per member it runs the universal safety check on the original, applies the policy
transform (and optional user filter) to a transient copy, and writes FILE / DIR / SYMLINK
/ HARDLINK, tracking bomb limits and per-member results.

Hardlinks (TAR ordering guarantees the source precedes its links) are resolved by one
**core** algorithm: a sequential forward pass with a conditional second pass. When a
filter/selector orphans a selected link (its source excluded), a re-readable (seekable)
source is recovered in a single second pass; a forward-only source is unrecoverable and
fails per ``OnError``. See ``safe-extraction`` / ``format-tar`` for the normative spec.

The optional *planned single pass* optimization (staging an excluded source during the
first pass when a free member list exists) is deliberately not implemented here — it is an
optimization over this core, not a correctness requirement.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Callable, Collection, assert_never

from archivey.config import ExtractionLimits
from archivey.exceptions import (
    ArchiveyError,
    DiagnosticRaisedError,
    ExtractionError,
    FilterRejectionError,
    LinkTargetNotFoundError,
    NameCollisionError,
    NameRewrittenError,
    ResourceLimitError,
    SymlinkEscapeError,
)
from archivey.internal.filters import (
    POLICY_TRANSFORMS,
    apply_name_policy,
    check_universal,
    collision_key,
)
from archivey.internal.logs import extraction as logger
from archivey.internal.selection import (
    CollectionSelector,
    normalize_member_selector,
)
from archivey.terminal import display_path, quoted
from archivey.types import (
    AbortOn,
    ArchiveMember,
    ExtractionPolicy,
    ExtractionProgress,
    ExtractionResult,
    ExtractionStatus,
    MemberFilter,
    MemberSelectorArg,
    MemberType,
    OnError,
    OverwritePolicy,
)

if TYPE_CHECKING:
    from archivey.internal.base_reader import BaseArchiveReader


_CHUNK = 1024 * 1024  # 1 MiB copy chunk

# Prefix of the temp files atomic FILE writes stage in the destination directory
# (mkstemp appends a random suffix). Python-level failures always unlink them, but a
# hard kill (SIGKILL, power loss) cannot: leftover ``.archivey-tmp-*`` files in an
# extraction destination are archivey's and are safe to delete. Documented in
# docs/safe-extraction.md; keep the value and that doc in sync.
_TMP_PREFIX = ".archivey-tmp-"

# Defaults (see the safe-extraction spec); callers override via extract()/extract_all().
DEFAULT_MAX_EXTRACTED_BYTES = 2 * 2**30  # 2 GiB
DEFAULT_MAX_RATIO = 1000.0
DEFAULT_RATIO_ACTIVATION_THRESHOLD = 5 * 2**20  # 5 MiB
DEFAULT_MAX_ENTRIES = 1_048_576  # 2**20


def _open_new_file(path: Path, mode: int) -> int:
    """Create ``path``, which must not exist, for writing; return the open fd.

    ``mode`` is the creation mode, so the umask applies to it as it does to any new
    file. The flags are ``mkstemp``'s: ``O_EXCL`` fails loudly on a name that appeared
    underneath us, ``O_NOFOLLOW`` refuses a symlink planted there, and ``O_BINARY`` /
    ``O_NOINHERIT`` matter on Windows only.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for extra in ("O_NOFOLLOW", "O_BINARY", "O_NOINHERIT"):
        flags |= getattr(os, extra, 0)
    return os.open(path, flags, mode)


class _AbortExtraction(Exception):
    """Internal carrier for an ``abort_on`` trigger fired deep in the write path.

    The public error it wraps (``NameCollisionError`` / ``NameRewrittenError``) is an
    ``ExtractionError``, so raising it directly would be caught by the per-member
    handlers and turned into a ``FAILED`` result — the opposite of an abort. This
    carrier is a plain ``Exception``, so it passes those handlers untouched (their
    ``finally`` clauses still close streams) and ``run()`` unwraps it at the top.
    """

    def __init__(self, error: ArchiveyError) -> None:
        super().__init__(str(error))
        self.error = error


class _AlwaysStopResourceLimitError(ResourceLimitError):
    """A cumulative/global bomb guard: halts extraction even under ``OnError.CONTINUE``.

    A subclass of ``ResourceLimitError`` so callers catching that type (or
    ``ArchiveyError``) still catch it; the coordinator uses the type to distinguish it
    from a *skippable* per-member ratio failure.
    """


# Backward-compatible alias used by older tests/imports.
_AlwaysStopExtractionError = _AlwaysStopResourceLimitError


class BombTracker:
    """Cumulative byte / per-member ratio / archive-wide ratio / entry-count guards.

    Constructed once per extraction call. ``start_member()`` is called (with the
    **original** member) before each member; ``count()`` is called with each chunk
    written.
    """

    def __init__(
        self,
        max_bytes: int | None,
        max_ratio: float | None,
        ratio_activation_threshold: int = DEFAULT_RATIO_ACTIVATION_THRESHOLD,
        max_entries: int | None = DEFAULT_MAX_ENTRIES,
        *,
        source: "BaseArchiveReader | None" = None,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_ratio = max_ratio
        self._ratio_floor = ratio_activation_threshold
        # Archive-wide ratio denominators, taken from the reader so the coordinator just
        # hands over the source: the static outer compressed size when it is cheaply known
        # (captured once), otherwise a LIVE sample of the compressed bytes consumed from the
        # source (read fresh on each count(), since it grows as extraction proceeds).
        self._source = source
        self._compressed_source_size = (
            source.compressed_source_size if source is not None else None
        )
        self._max_entries = max_entries
        self._entry_count = 0
        self._total_bytes = 0  # decoded output, cumulative across all members
        # Bytes copied from a file this run already wrote (the cross-device hardlink
        # fallback). They count toward the byte cap, not the ratios.
        self._copied_bytes = 0
        self._member_bytes = 0  # output bytes for the current member
        self._member: ArchiveMember | None = None
        # Output of members a streaming pass wrote and then took back as superseded (see
        # ``refund``). Subtracted for the byte cap only.
        self._refunded_bytes = 0

    @property
    def total_bytes(self) -> int:
        """Every byte written to the destination, decoded or copied."""
        return self._total_bytes + self._copied_bytes

    @property
    def member_bytes(self) -> int:
        return self._member_bytes

    def start_member(self, member: ArchiveMember) -> None:
        # Called with the ORIGINAL member (not a filter copy), so compressed_size and any
        # late-bound fields are accurate. Increments the entry-count guard, which — like
        # the cumulative byte guard — halts even under OnError.CONTINUE.
        self._member = member
        self._member_bytes = 0
        self._entry_count += 1
        if self._max_entries is not None and self._entry_count > self._max_entries:
            raise _AlwaysStopResourceLimitError(
                f"Entry-count limit reached: max_entries={self._max_entries} "
                f"(would write entry {self._entry_count})"
            )

    def refund(self, member_bytes: int) -> None:
        """Stop counting one member that a streaming pass took back as superseded.

        Random access never writes a shadowed duplicate, so it never counts one
        (``safe-extraction``: no bomb-limit counting for the skip). A streaming pass
        wrote it before it could know, and its entry is gone from the destination once
        the later copy replaces it, so the entry count stops counting it. Its bytes are
        gone too unless a hardlink made to it still holds them, so the caller passes
        ``member_bytes`` as 0 in that case and the byte cap keeps them. The archive-wide
        ratio counts them either way: those bytes were decoded, and a name repeated many
        times must not decode for free. ``total_bytes``, which progress reports, is not
        reduced: it counts what was written.
        """
        self._entry_count -= 1
        self._refunded_bytes += member_bytes

    def count(self, chunk_bytes: int) -> None:
        self._total_bytes += chunk_bytes
        self._member_bytes += chunk_bytes
        self._check_cumulative_bytes()

        # Per-member ratio: activates on THIS member's output; a per-member failure
        # (skippable under OnError.CONTINUE).
        member = self._member
        cs = member.compressed_size if member is not None else None
        if (
            self._max_ratio is not None
            and member is not None
            and self._member_bytes > self._ratio_floor
            and cs
            and cs > 0
        ):
            ratio = self._member_bytes / cs
            if ratio > self._max_ratio:
                raise ResourceLimitError(
                    f"Decompression ratio {ratio:.0f}:1 exceeds limit "
                    f"max_ratio={self._max_ratio:.0f}:1",
                    member_name=member.name,
                )
        self._check_archive_ratio()

    def count_copy(self, chunk_bytes: int) -> None:
        """Count bytes copied from a file this run already wrote, not decoded.

        The cross-device hardlink fallback writes a second full copy of content already
        on disk. Those bytes land on the filesystem, so they count toward
        ``max_extracted_bytes`` like any other write. Nothing decompressed them, so
        neither ratio sees them: whether a copy happens depends on where the
        destination's mount points fall, not on the archive, and a ratio abort would
        blame the archive for it.
        """
        self._copied_bytes += chunk_bytes
        self._check_cumulative_bytes()

    def _check_cumulative_bytes(self) -> None:
        # Cumulative byte guard (always-stop).
        written = self.total_bytes - self._refunded_bytes
        if self._max_bytes is not None and written > self._max_bytes:
            raise _AlwaysStopResourceLimitError(
                f"Extraction limit reached: max_extracted_bytes={self._max_bytes} "
                f"(written {written} bytes)"
            )

    def _check_archive_ratio(self) -> None:
        # Archive-wide ratio: activates on CUMULATIVE output; a whole-archive bomb signal
        # (always-stop). Uses the static outer compressed size when it is cheaply known,
        # otherwise a LIVE denominator — the compressed bytes consumed from the source so
        # far — which works for a streaming/pipe source whose total size is unknown. The two
        # are mutually exclusive (static when known, live otherwise), so the ratio is never
        # counted twice.
        css = self._compressed_source_size
        if self._max_ratio is not None and self._total_bytes > self._ratio_floor:
            if css and css > 0:
                if self._total_bytes / css > self._max_ratio:
                    raise _AlwaysStopResourceLimitError(
                        f"Archive-wide decompression ratio "
                        f"{self._total_bytes / css:.0f}:1 exceeds limit "
                        f"max_ratio={self._max_ratio:.0f}:1"
                    )
            elif self._source is not None:
                consumed = self._source.compressed_bytes_consumed
                if (
                    consumed
                    and consumed > 0
                    and self._total_bytes / consumed > self._max_ratio
                ):
                    raise _AlwaysStopResourceLimitError(
                        f"Live decompression ratio "
                        f"{self._total_bytes / consumed:.0f}:1 exceeds limit "
                        f"max_ratio={self._max_ratio:.0f}:1"
                    )


@dataclass
class _Orphan:
    """A selected hardlink whose source was not yet on disk, awaiting the second pass.

    ``transformed`` is the policy/filter-transformed copy from the first pass: it supplies
    the on-disk identity (mode, timestamps) when the source's content is materialized at
    this link's path (see the ``safe-extraction`` "copy supplies the identity" rule).
    """

    result_index: int
    original: ArchiveMember
    transformed: ArchiveMember
    dest_path: Path
    source: ArchiveMember


@dataclass(frozen=True)
class _Claim:
    """A destination claimed by a member written this run.

    The path alone was enough while a collision only had to be *detected*; recording the
    claiming member's result index as well is what lets a later ``REPLACE`` collision
    revise the earlier result to ``OVERWRITTEN`` instead of leaving two members both
    reporting ``EXTRACTED`` at one path.
    """

    path: Path
    result_index: int


class ExtractionCoordinator:
    """Drives a single forward pass over a reader's members, writing them safely to disk."""

    def __init__(
        self,
        *,
        policy: ExtractionPolicy = ExtractionPolicy.STRICT,
        overwrite: OverwritePolicy = OverwritePolicy.ERROR,
        on_error: OnError = OnError.STOP,
        on_progress: Callable[[ExtractionProgress], None] | None = None,
        members: MemberSelectorArg = None,
        filter: MemberFilter | None = None,
        limits: ExtractionLimits | None = None,
        abort_on: Collection[AbortOn] = (),
    ) -> None:
        self._policy = policy
        self._overwrite = overwrite
        self._on_error = on_error
        self._abort_on = frozenset(abort_on)
        self._on_progress = on_progress
        self._members = members
        self._filter = filter
        self._limits = limits if limits is not None else ExtractionLimits()
        # Intra-member progress emit while copying a FILE (None when on_progress is unset
        # or the current member has no streamed body). Built by ``run()`` per member.
        self._emit_progress: Callable[[], None] | None = None
        # The destination the current member asked for, published so the per-member error
        # handler can record it on a FAILED/BLOCKED result. A collision resolved by ERROR
        # raises out of the write, which is exactly the case where the spec still requires
        # ``requested_path`` set with ``path=None``.
        self._requested_path: Path | None = None
        self._collided_with: Path | None = None
        # Set by ``_transform`` when reading a link's target showed the current member
        # is not a link after all, so the pass yielded it with no data stream.
        self._retyped: bool = False
        # Set by ``_prepare_destination`` when it removes an existing entry to make room.
        # Only the non-atomic paths (DIR / SYMLINK / HARDLINK) do that — a FILE write
        # lands via os.replace and never destroys the destination up front — so this is
        # how the write path knows a failure afterwards left a hole rather than the old
        # content. Read and reset per member.
        self._removed_existing = False
        # Running count of EXTRACTED results, on the coordinator rather than in the pass
        # loop because a REPLACE collision revises an *earlier* member's result and must
        # correct the tally with it (progress reports tallies of results, not of writes
        # attempted). Reset per ``run()``.
        self._members_extracted = 0
        # The same for BLOCKED results, which a streaming pass's take-back of a
        # superseded copy can revise.
        self._members_blocked = 0
        # RENAME: the last ``N`` tried per collision key of the requested name, so the
        # next member colliding on that key resumes after it instead of rescanning from
        # ``(1)``. Reset per ``run()``, and cleared whenever a claim is released (a freed
        # name may be the first free one again).
        self._rename_next: dict[str, int] = {}
        # A streaming pass's superseded copies, left in place while the later copy of the
        # same name is handled so that copy can replace one atomically: path -> index of
        # the superseded result. Only for the length of one member; see
        # ``_supersede_written_copy``.
        self._stale: dict[Path, int] = {}
        # Superseded copies the filesystem refused to remove, by archive name. The next
        # member of that name parks them in ``_stale`` again, so it can still replace
        # the run's own leftover instead of meeting it as a pre-existing entry.
        self._unremoved: dict[str, dict[Path, int]] = {}
        # The reader ``run()`` is extracting from, for the one read ``_transform`` makes
        # on it: an accepted link's target. Set per ``run()``.
        self._reader: BaseArchiveReader | None = None

    # --- entry point ---------------------------------------------------------------

    def run(
        self, reader: "BaseArchiveReader", dest: str | Path
    ) -> list[ExtractionResult]:
        dest = Path(dest)
        forward_only = reader._streaming
        self._rename_next = {}
        self._stale = {}
        self._unremoved = {}

        tracker = BombTracker(
            self._limits.max_extracted_bytes,
            self._limits.max_ratio,
            self._limits.ratio_activation_threshold,
            self._limits.max_entries,
            source=reader,
        )

        selector = normalize_member_selector(self._members)
        self._reader = reader

        # Progress totals cover what this call will actually attempt: when a member list
        # is free (an upfront index) and a selector is given, totals count only the
        # selected members — so members_done can reach members_total and the byte
        # estimate matches the selected output. The user `filter` runs only during
        # extraction and cannot be pre-applied, so members it skips still count as
        # processed below. Streaming readers with no free list report None totals.
        members_report = reader.members_report_if_available()
        all_members = list(members_report) if members_report is not None else None
        if all_members is not None and selector is not None:
            all_members = [m for m in all_members if selector(m)]
        # A members= collection whose entries all went through the free list is
        # settled now: report the entries that matched nothing before anything is
        # written or created, so a RAISE disposition refuses the call with nothing on
        # disk.
        # Without a free list the answer is known only at the end of the pass.
        unmatched_pending: CollectionSelector | None = None
        if isinstance(selector, CollectionSelector):
            if all_members is not None:
                selector.report_unmatched(
                    reader._diagnostics_collector, reader._archive_name
                )
            else:
                unmatched_pending = selector
        # Created only after that report, so a refusal leaves no directory behind.
        self._ensure_dest_root(dest)
        dest_root = dest.resolve()
        members_total = len(all_members) if all_members is not None else None
        total_estimate = self._estimate_total_bytes(all_members)

        # Extract-prep materialization: enforce ListingLimits before writing.
        # Indexed backends may already have been peeked via members_report_if_available();
        # scan-required backends (TAR, directory) would otherwise walk via unguarded
        # stream_members() and never hit listing caps.
        if not forward_only:
            reader._get_members_registered(enforce_listing_limits=True)

        # The pass is driven through the public stream_members(), which applies the
        # selection (skipped members never surface here — they are invisible to progress
        # and results, matching the totals above). When the totals pre-filtered the free
        # member list, that list is reused as an identity selector, so a user predicate
        # runs once per member (on the index) rather than once per pre-filter plus once
        # per yield; a stateful predicate still sees each member a single time. Without
        # a free list the predicate itself is passed through.
        #
        # Every pass the coordinator drives hands stream_members() a CollectionSelector
        # built here, with no bookkeeping. A selector that stream_members() builds from a
        # collection reports its own unmatched entries, and a collection built here is
        # the coordinator's, not the caller's (the hardlink second pass does the same).
        stream_selector = (
            None
            if selector is None
            else CollectionSelector(list(all_members), record=False)
            if all_members is not None
            else selector
        )

        results: list[ExtractionResult] = []
        # source member_id -> list of on-disk paths holding that source's content.
        source_paths: dict[int, list[Path]] = {}
        written_paths: set[Path] = set()
        # O2 collision map: casefold(NFC(relpath)) key (exact under TRUSTED) -> the claim
        # (written path + claiming member's result index). Tracks non-directory members
        # written THIS run so a second member resolving to the same key is a deterministic
        # collision on every OS (not a platform-dependent silent merge), and so a REPLACE
        # resolution can revise the earlier member's result. See _write_member /
        # dev-docs/decisions/0013.
        collision_map: dict[str, _Claim] = {}
        orphans: list[_Orphan] = []
        # Explicit selection with a free member list: once every selected member has
        # been seen, stop the forward pass. Solid backends otherwise keep walking the
        # rest of the archive (and would skip-decode unread tails if positioning were
        # eager). Predicate selectors and streaming backends have no finite remaining
        # count, so they still drain.
        selected_total = (
            members_total
            if selector is not None and members_total is not None
            else None
        )

        try:
            self._run_pass(
                reader,
                stream_selector,
                dest,
                dest_root,
                tracker,
                results,
                source_paths,
                written_paths,
                collision_map,
                orphans,
                forward_only,
                members_total,
                total_estimate,
                selected_total,
            )
        except _AbortExtraction as abort:
            # An abort_on trigger fired: no report is returned, and output already
            # written for earlier members stays on disk (same as OnError.STOP). Nothing
            # partial exists for the triggering member — every trigger fires before its
            # write begins, and FILE writes land atomically in any case.
            raise abort.error from None

        if unmatched_pending is not None:
            unmatched_pending.report_unmatched(
                reader._diagnostics_collector, reader._archive_name
            )
        return results

    def _run_pass(
        self,
        reader: "BaseArchiveReader",
        stream_selector: MemberSelectorArg,
        dest: Path,
        dest_root: Path,
        tracker: BombTracker,
        results: list[ExtractionResult],
        source_paths: dict[int, list[Path]],
        written_paths: set[Path],
        collision_map: dict[str, _Claim],
        orphans: list[_Orphan],
        forward_only: bool,
        members_total: int | None,
        total_estimate: int | None,
        selected_total: int | None,
    ) -> None:
        """The forward pass and the orphan second pass, appending into ``results``.

        Split out of ``run()`` only so an ``abort_on`` trigger raised anywhere inside it
        has one place to unwind to; ``results`` is filled in place because an abort
        discards it rather than returning it.
        """
        members_done = 0
        self._members_blocked = 0
        self._members_extracted = 0
        # Archive name -> result index of the latest member of that name that went on to
        # be written (or tried). Random access stamps last-entry-wins before the pass, so
        # a shadowed copy reaches the SUPERSEDED branch below and is never recorded here.
        # A streaming pass learns of the later copy only when it arrives; see
        # ``_supersede_written_copy``.
        current_by_name: dict[str, int] = {}
        # Result index -> output bytes counted for it, for each member that reached
        # ``tracker.start_member``: what a take-back refunds.
        counted: dict[int, int] = {}

        for original, stream in reader.stream_members(stream_selector):
            member_started = False
            recorded_index: int | None = None
            presented_name: str | None = None
            self._emit_progress = None
            self._requested_path = None
            self._collided_with = None
            self._retyped = False
            try:
                for held, held_index in self._unremoved.pop(original.name, {}).items():
                    # Unless another member has since replaced it under its own claim.
                    key = collision_key(self._rel_name(dest, held), self._policy)
                    if collision_map.get(key) == _Claim(held, held_index):
                        self._stale[held] = held_index
                        del collision_map[key]
                earlier = current_by_name.get(original.name)
                if earlier is not None:
                    self._supersede_written_copy(
                        earlier,
                        results,
                        tracker,
                        counted,
                        source_paths,
                        written_paths,
                        collision_map,
                        orphans,
                        dest,
                    )
                    del current_by_name[original.name]
                # User filter sees every selected member (including non-current); the
                # is_current skip is hardwired after the filter and does not force a write
                # even if the filter returns the member.
                transformed, presented_name = self._transform(original, dest_root)
                if transformed is None:
                    # Filter returned None: caller-elected exclusion — no ExtractionResult
                    # (same as a selector exclusion). Still counts as processed for progress.
                    pass
                elif not original.is_current:
                    recorded_index = len(results)
                    results.append(
                        ExtractionResult(
                            original, None, ExtractionStatus.SUPERSEDED, None
                        )
                    )
                else:
                    result_index = recorded_index = len(results)
                    current_by_name[original.name] = result_index
                    results.append(
                        ExtractionResult(original, None, ExtractionStatus.FAILED, None)
                    )
                    if original.is_anti:
                        results[result_index] = self._write_member(
                            original,
                            transformed,
                            stream,
                            dest,
                            dest_root,
                            tracker,
                            source_paths,
                            written_paths,
                            collision_map,
                            orphans,
                            forward_only,
                            result_index,
                            results,
                        )
                    else:
                        # Entry-count guard + ratio bookkeeping. Counted only once the
                        # selector and user filter have accepted the member (and the
                        # universal check inside _transform has passed), immediately before
                        # writing begins — so selector-skipped, filter-skipped, and rejected
                        # members create nothing on disk and do not count toward max_entries
                        # (resolved 2026-07 decision).
                        tracker.start_member(original)
                        member_started = True
                        if self._on_progress is not None:
                            # Capture counters for intra-member reports: members_done is
                            # members fully completed *before* this one.
                            done_so_far = members_done
                            extracted_so_far = self._members_extracted
                            blocked_so_far = self._members_blocked
                            current = original

                            def emit_progress() -> None:
                                self._report_progress(
                                    current,
                                    tracker,
                                    total_estimate,
                                    done_so_far,
                                    members_total,
                                    member_bytes_written=tracker.member_bytes,
                                    members_extracted=extracted_so_far,
                                    members_blocked=blocked_so_far,
                                )

                            self._emit_progress = emit_progress

                        results[result_index] = self._write_member(
                            original,
                            transformed,
                            stream,
                            dest,
                            dest_root,
                            tracker,
                            source_paths,
                            written_paths,
                            collision_map,
                            orphans,
                            forward_only,
                            result_index,
                            results,
                        )
            except (_AlwaysStopResourceLimitError, DiagnosticRaisedError):
                raise
            except (ArchiveyError, OSError) as exc:
                # A name the universal filter accepted (it is fsencodable) but that the
                # *destination filesystem* refuses at write time — non-UTF-8 bytes on a
                # UTF-8-enforcing FS such as APFS/macOS raise OSError EILSEQ ("Illegal
                # byte sequence") — is not a generic I/O failure. Surface it as a typed
                # extraction error so callers get "this name is not representable here"
                # instead of a bare OSError. (Rewriting such names to an always-portable
                # spelling so the write succeeds is the separate, policy-gated
                # threat-model O7 follow-up.)
                error: ArchiveyError | OSError = exc
                if isinstance(exc, OSError) and exc.errno == errno.EILSEQ:
                    error = ExtractionError(
                        "Member name cannot be represented on the destination "
                        "filesystem",
                        member_name=original.name,
                    )
                    error.__cause__ = exc
                status = (
                    ExtractionStatus.BLOCKED
                    if isinstance(error, FilterRejectionError)
                    else ExtractionStatus.FAILED
                )
                result = ExtractionResult(
                    original,
                    None,
                    status,
                    error,
                    requested_path=self._requested_path,
                    collided_with=self._collided_with,
                )
                if results and results[-1].member is original:
                    recorded_index = len(results) - 1
                    results[-1] = result
                else:
                    recorded_index = len(results)
                    results.append(result)
                # OnError governs failures only; a policy BLOCKED is always continued.
                if self._stops_on_failure() and status is ExtractionStatus.FAILED:
                    raise error
                # ...unless the caller asked to be stopped by an unsafe member. This is
                # the fail-closed strict-security opt-in; it applies under either OnError
                # value, and propagates the original rejection unchanged. The BLOCKED
                # result recorded just above is discarded with the rest of the report.
                if (
                    status is ExtractionStatus.BLOCKED
                    and AbortOn.BLOCKED_MEMBER in self._abort_on
                ):
                    raise error
                # No diagnostic: the result recorded above is the whole record of this
                # outcome (the placement clause in ``diagnostics``). The WARNING log line
                # that used to be the emission's projection goes out directly.
                logger.warning(
                    "Skipping %s %r: %s", original.type.value, original.name, error
                )
            finally:
                self._emit_progress = None
                self._close(stream)
                if self._stale:
                    self._drop_stale_copies(
                        original, results, written_paths, collision_map, dest
                    )

            if member_started and recorded_index is not None:
                counted[recorded_index] = tracker.member_bytes

            # A portable rewrite is recorded on whatever result this member ended up with,
            # including a BLOCKED/FAILED one: the rewrite happened before the outcome.
            if presented_name is not None and recorded_index is not None:
                results[recorded_index] = replace(
                    results[recorded_index], presented_name=presented_name
                )

            if results and results[-1].member is original:
                status = results[-1].status
                if status is ExtractionStatus.EXTRACTED:
                    self._members_extracted += 1
                elif status is ExtractionStatus.BLOCKED:
                    self._members_blocked += 1
            members_done += 1
            self._report_progress(
                original,
                tracker,
                total_estimate,
                members_done,
                members_total,
                member_bytes_written=(tracker.member_bytes if member_started else 0),
                members_extracted=self._members_extracted,
                members_blocked=self._members_blocked,
            )
            if selected_total is not None and members_done >= selected_total:
                break

        # Core second pass: resolve orphaned hardlinks whose (re-readable) source was
        # excluded. Only populated for a seekable source; forward-only orphans already
        # failed at the link during the main pass.
        if orphans:
            self._resolve_orphans(
                reader, source_paths, orphans, tracker, results, collision_map, dest
            )

    def _supersede_written_copy(
        self,
        index: int,
        results: list[ExtractionResult],
        tracker: BombTracker,
        counted: dict[int, int],
        source_paths: dict[int, list[Path]],
        written_paths: set[Path],
        collision_map: dict[str, _Claim],
        orphans: list[_Orphan],
        dest: Path,
    ) -> None:
        """Take back an earlier copy of a name the archive holds again (streaming only).

        Random access knows every duplicate before it writes anything, so the shadowed
        copy is reported ``SUPERSEDED`` and never written. A streaming pass finds out
        when the later copy arrives, after the earlier one was already handled. The
        earlier result becomes ``SUPERSEDED`` here, before the later copy is filtered:
        random access supersedes it whatever then happens to the later copy.

        The earlier copy's file stays where it is while the later copy is handled, as
        ``_stale``: the destination checks treat that path as free, so the later
        copy replaces it atomically, under any overwrite policy. If the later copy does
        not land there, ``_drop_stale_copies`` removes it once the member is done. Its
        claim, its place in the hardlink source lists and its bomb-limit counts are
        released now. A hardlink already made to it is its own directory entry and
        stays, and its bytes stay counted against the byte cap while it holds them. A
        directory is removed now if it is empty; one that other members were written
        into stays, as their parent, as it would in random access. An orphaned hardlink
        waiting on the second pass is dropped with the result it would fill. An error
        recorded on the earlier result is dropped with it: random access never tried
        that copy.
        """
        prior = results[index]
        path = prior.path
        # Whether another directory entry still holds the earlier copy's content: a
        # hardlink made to it before the later copy arrived.
        content_kept = False
        if (
            prior.status is ExtractionStatus.EXTRACTED
            and path is not None
            and path in written_paths
        ):
            self._release_claim(collision_map, dest, path)
            for source_id, paths in list(source_paths.items()):
                if path in paths:
                    paths.remove(path)
                    if paths:
                        content_kept = True
                    else:
                        del source_paths[source_id]
            if path.is_dir() and not path.is_symlink():
                with contextlib.suppress(OSError):  # not empty: members live under it
                    os.rmdir(path)
                    written_paths.discard(path)
            else:
                written_paths.discard(path)
                self._stale[path] = index
        if index in counted:
            member_bytes = counted.pop(index)
            tracker.refund(0 if content_kept else member_bytes)
        # Progress tallies results, so they follow the revision (as in
        # ``_mark_overwritten``).
        if prior.status is ExtractionStatus.EXTRACTED:
            self._members_extracted -= 1
        elif prior.status is ExtractionStatus.BLOCKED:
            self._members_blocked -= 1
        orphans[:] = [o for o in orphans if o.result_index != index]
        results[index] = ExtractionResult(
            prior.member,
            None,
            ExtractionStatus.SUPERSEDED,
            None,
            presented_name=prior.presented_name,
        )

    def _drop_stale_copies(
        self,
        original: ArchiveMember,
        results: list[ExtractionResult],
        written_paths: set[Path],
        collision_map: dict[str, _Claim],
        dest: Path,
    ) -> None:
        """Remove each parked superseded copy unless the member just handled replaced it.

        A removal the filesystem refuses is logged, not raised: this runs in a
        ``finally``, where raising would replace the member's own error. The entry is
        then still the run's own, so it goes back into ``written_paths`` (an anti-item
        can still delete it) and into the collision map (a different name on the same
        key still collides with it), and is held for the next member of the same name.
        Its claim points at its ``SUPERSEDED`` result, which ``_mark_overwritten``
        leaves as it is.
        """
        stale, self._stale = self._stale, {}
        latest = results[-1] if results else None
        landed = (
            latest.path
            if latest is not None
            and latest.member is original
            and latest.status is ExtractionStatus.EXTRACTED
            else None
        )
        for path, index in stale.items():
            if path == landed:
                continue
            try:
                os.unlink(path)
            except FileNotFoundError:
                written_paths.discard(path)
            except OSError as exc:
                logger.warning("Could not remove superseded %r: %s", str(path), exc)
                written_paths.add(path)
                key = collision_key(self._rel_name(dest, path), self._policy)
                collision_map[key] = _Claim(path, index)
                self._unremoved.setdefault(original.name, {})[path] = index
            else:
                written_paths.discard(path)

    def _occupied(self, path: Path) -> bool:
        """Whether ``path`` holds an entry, not counting a superseded copy in waiting."""
        return path not in self._stale and os.path.lexists(path)

    # --- selection / transform -----------------------------------------------------

    def _transform(
        self, original: ArchiveMember, dest_root: Path
    ) -> tuple[ArchiveMember | None, str | None]:
        """Universal check on the original, then policy transform and user filter on a
        transient copy.

        Returns ``(member_to_write, presented_name)`` — the member is ``None`` if the user
        filter skipped it, and ``presented_name`` is the pre-rewrite full name when the
        portable-name policy rewrote it, else ``None``. Raises a ``FilterRejectionError``
        on a universal violation."""
        check_universal(original, dest_root)
        transformed = POLICY_TRANSFORMS[self._policy](original)
        if self._filter is not None:
            transformed = self._filter(transformed)
            if transformed is None:
                return None, None
            # A caller filter can rename/relink; re-run the universal check on the result.
            check_universal(transformed, dest_root)
        if self._reader is not None and self._needs_target_read(original, transformed):
            # A symlink whose target the format keeps in member data and nothing has
            # read yet: `read_link_targets=False`, or a streaming pass whose own read
            # comes only at EOF. The selector and the filter have now both accepted it,
            # with `link_target=None`, so reading it here is the read the caller asked
            # for (`archive-reading`, "Link targets stored as member data are read only
            # when configured"). The target it yields is checked like any other below.
            self._reader._read_link_target_on_request(original)
            if original.type is not MemberType.SYMLINK:
                # The data showed the member is not a link: a reparse-flagged member
                # whose data is no reparse buffer, re-typed to its fallback as listing
                # would have. Everything above decided on a member that did not exist,
                # so decide again on the real one, the filter included.
                self._retyped = True
                return self._transform(original, dest_root)
            if original.link_target is not None:
                if transformed is not original:
                    transformed = transformed.replace(link_target=original.link_target)
                check_universal(transformed, dest_root)
        # Portable-name policy on the FINAL name — after the user filter, so a filter rename
        # is checked too, and TRUSTED keeps faithful bytes. Reserved names / ':' are
        # rejected; a trailing dot/space (STRICT) or non-representable byte is rewritten to a
        # portable spelling, recorded on the result so the rename is not silent.
        portable = apply_name_policy(transformed, self._policy)
        if portable.name == transformed.name:
            return portable, None
        # The pre-rewrite spelling is the caller filter's output when there is one, which
        # is why it cannot be reconstructed from ``member.name`` and ``path`` alone.
        if AbortOn.NAME_SANITIZED in self._abort_on:
            raise _AbortExtraction(
                NameRewrittenError(
                    f"Name rewritten for portability: {quoted(transformed.name)} -> "
                    f"{quoted(portable.name)}",
                    member_name=original.name,
                )
            )
        return portable, transformed.name

    @staticmethod
    def _needs_target_read(original: ArchiveMember, transformed: ArchiveMember) -> bool:
        """Whether a link about to be written still needs its target read.

        Only a current symlink the caller's filter left targetless and whose target
        nobody has looked for yet: a superseded member is not written, and a target the
        filter supplied is the one to write.
        """
        return (
            original.type is MemberType.SYMLINK
            and original.is_current
            and original.link_target is None
            and not original._link_target_resolved
            and transformed.link_target is None
        )

    # --- per-member write ----------------------------------------------------------

    def _write_member(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        stream: BinaryIO | None,
        dest: Path,
        dest_root: Path,
        tracker: BombTracker,
        source_paths: dict[int, list[Path]],
        written_paths: set[Path],
        collision_map: dict[str, _Claim],
        orphans: list[_Orphan],
        forward_only: bool,
        result_index: int,
        results: list[ExtractionResult],
    ) -> ExtractionResult:
        requested = dest / transformed.name
        self._requested_path = requested

        if original.is_anti:
            return replace(
                self._apply_anti_item(
                    original, requested, written_paths, collision_map, dest
                ),
                requested_path=requested,
            )

        dest_path, prior, collided_with = self._resolve_collision(
            original, transformed, requested, collision_map, dest
        )
        redirected = prior is not None
        # Stashed for the failure handler: an ERROR-policy collision becomes a FAILED
        # result built there, and it has to carry the collision too.
        self._collided_with = collided_with

        self._removed_existing = False
        try:
            result = self._dispatch_write(
                original,
                transformed,
                stream,
                dest_path,
                dest_root,
                tracker,
                source_paths,
                written_paths,
                orphans,
                forward_only,
                result_index,
            )
        except (ArchiveyError, OSError):
            # The write failed *after* clearing the destination to make room for it (only
            # reachable on the non-atomic DIR/SYMLINK/HARDLINK paths). The earlier
            # member's content is gone either way, so its result must stop advertising a
            # live path — reporting EXTRACTED for bytes that no longer exist is exactly
            # the defect this change exists to remove. This member's own FAILED result is
            # recorded by the caller's handler.
            if self._removed_existing:
                self._mark_overwritten(results, prior)
            raise

        # Record the intended destination. A REPLACE merge into a prior path is not a
        # rename, so it reports the actual (merged) path; every other outcome reports the
        # member's own intended destination, so RENAME shows up as requested_path != path.
        if redirected and result.status is ExtractionStatus.EXTRACTED:
            result = replace(
                result, requested_path=result.path, collided_with=collided_with
            )
        else:
            result = replace(
                result, requested_path=requested, collided_with=collided_with
            )

        # A REPLACE that actually landed leaves the earlier member's content gone. Revise
        # that member's already-recorded result to OVERWRITTEN so the merge is visible in
        # ``results`` instead of two members both reporting EXTRACTED at one path.
        if result.status is ExtractionStatus.EXTRACTED:
            self._mark_overwritten(results, prior)

        self._register_collision_key(
            collision_map, dest, transformed, result, result_index
        )
        return result

    def _dispatch_write(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        stream: BinaryIO | None,
        dest_path: Path,
        dest_root: Path,
        tracker: BombTracker,
        source_paths: dict[int, list[Path]],
        written_paths: set[Path],
        orphans: list[_Orphan],
        forward_only: bool,
        result_index: int,
    ) -> ExtractionResult:
        if transformed.type == MemberType.DIRECTORY:
            existed = self._occupied(dest_path)
            if not self._prepare_destination(transformed, dest_path):
                return ExtractionResult(
                    original, None, ExtractionStatus.NOT_OVERWRITTEN, None
                )
            os.makedirs(dest_path, exist_ok=True)
            self._apply_metadata(dest_path, transformed)
            if not existed:
                written_paths.add(dest_path)
            return ExtractionResult(
                original, dest_path, ExtractionStatus.EXTRACTED, None
            )

        if transformed.type == MemberType.SYMLINK:
            result = self._write_symlink(original, transformed, dest_root, dest_path)
            if result.status is ExtractionStatus.EXTRACTED and result.path is not None:
                written_paths.add(result.path)
            return result

        if transformed.type == MemberType.HARDLINK:
            result = self._write_hardlink(
                original,
                transformed,
                dest_path,
                tracker,
                source_paths,
                orphans,
                forward_only,
                result_index,
            )
            if result.status is ExtractionStatus.EXTRACTED and result.path is not None:
                written_paths.add(result.path)
            return result

        if transformed.type == MemberType.FILE:
            result = self._write_file(
                original, transformed, stream, dest_path, tracker, source_paths
            )
            if result.status is ExtractionStatus.EXTRACTED and result.path is not None:
                written_paths.add(result.path)
            return result

        # MemberType.OTHER is rejected by check_universal; nothing else should reach here.
        raise ExtractionError(
            f"Unsupported member type {transformed.type!r}",
            member_name=transformed.name,
        )

    def _resolve_collision(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        requested: Path,
        collision_map: dict[str, _Claim],
        dest: Path,
    ) -> tuple[Path, _Claim | None, Path | None]:
        """Resolve an O2 name collision, returning
        ``(dest_path, redirected_from, collided_with)``.

        ``redirected_from`` is the prior claim when this member was routed onto an
        already-written destination (so the caller can revise that member's result), and
        ``None`` otherwise — including under RENAME, which writes somewhere fresh.

        ``collided_with`` is the prior path whenever a collision *event* occurred, which
        is the wider set: RENAME collides and then avoids the destination, so it reports
        a path here while reporting no redirection. It is set under exactly the condition
        the removed ``EXTRACTION_NAME_COLLISION`` diagnostic fired on — a claim held by
        this run, outside TRUSTED — so the result carries the fact the diagnostic used to
        carry. A pre-existing on-disk obstacle is not a collision event and reports
        ``None``, as the diagnostic was also silent for it.

        Directories are structural (parents recur, entries merge) and are not tracked as
        content collisions. For a tracked member whose key is already claimed THIS run, a
        collision is deterministic on every OS: route ERROR/SKIP/REPLACE to the prior path
        so the existing OverwritePolicy machinery handles it uniformly, or derive a fresh
        ``name (N)`` under RENAME. TRUSTED keys on the exact name and defers to the local
        OS (no collision *event*, but RENAME still uses the map to avoid re-deriving
        names). Shared by the main pass and the orphan second pass so deferred hardlinks
        honour the map too.
        """
        if transformed.type == MemberType.DIRECTORY:
            return requested, None, None
        key = collision_key(self._rel_name(dest, requested), self._policy)
        prior = collision_map.get(key)
        collided = (
            prior.path
            if prior is not None and self._policy is not ExtractionPolicy.TRUSTED
            else None
        )
        if self._overwrite is OverwritePolicy.RENAME:
            if prior is not None or self._occupied(requested):
                if collided is not None:
                    assert prior is not None
                    self._check_collision_abort(original, transformed, prior)
                return (
                    self._derive_free_name(requested, transformed, collision_map, dest),
                    None,
                    collided,
                )
            return requested, None, None
        if collided is not None:
            assert prior is not None
            self._check_collision_abort(original, transformed, prior)
            return prior.path, prior, collided
        return requested, None, None

    def _stops_on_failure(self) -> bool:
        """Whether ``OnError`` halts on a member failure.

        Exhaustive on purpose: an ``OnError`` member this does not name raises instead
        of being treated as ``CONTINUE``, which would swallow its failures.
        """
        if self._on_error is OnError.STOP:
            return True
        if self._on_error is OnError.CONTINUE:
            return False
        assert_never(self._on_error)

    def _check_collision_abort(
        self, original: ArchiveMember, transformed: ArchiveMember, prior: _Claim
    ) -> None:
        """Abort on a collision event if the caller asked to be stopped by one.

        Fires on **every** non-TRUSTED collision whatever resolution follows — replaced,
        skipped, errored or renamed — because the trigger is the collision itself, not its
        outcome. Without the opt-in a collision is not an error: OverwritePolicy resolves
        it and both members' results record what happened.
        """
        if AbortOn.NAME_COLLISION not in self._abort_on:
            return
        raise _AbortExtraction(
            NameCollisionError(
                f"Name collision with already-written {display_path(prior.path)}",
                member_name=original.name,
            )
        )

    def _register_collision_key(
        self,
        collision_map: dict[str, _Claim],
        dest: Path,
        transformed: ArchiveMember,
        result: ExtractionResult,
        result_index: int,
    ) -> None:
        """Claim the key for an actually-written non-directory member so later members (in
        this pass and the orphan second pass) collide against it.

        The claim carries ``result_index`` so a later REPLACE onto this destination can
        revise this member's result to OVERWRITTEN. Re-claiming a key transfers it: after
        a replacement, a third member colliding revises the second, not the first."""
        if (
            transformed.type != MemberType.DIRECTORY
            and result.status is ExtractionStatus.EXTRACTED
            and result.path is not None
        ):
            key = collision_key(self._rel_name(dest, result.path), self._policy)
            collision_map[key] = _Claim(result.path, result_index)

    @staticmethod
    def _rel_name(dest: Path, path: Path) -> str:
        """The written path's location relative to the extraction root, for a collision key."""
        return path.relative_to(dest).as_posix()

    def _derive_free_name(
        self,
        requested: Path,
        transformed: ArchiveMember,
        collision_map: dict[str, _Claim],
        dest: Path,
    ) -> Path:
        """The first ``name (N)`` (N = 1, 2, …) free both in the collision map and on disk.

        The counter goes before the final suffix so the extension is preserved
        (``photo.jpg`` → ``photo (1).jpg``); a directory has no suffix and appends to the
        whole segment.

        The search resumes after the last ``N`` this run handed out for the same
        collision key, rather than starting at 1 each time: restarting made ``k``
        members colliding on one key cost ``k²`` probes, which a hostile archive of
        case-variant names turns into hours. The counter records names handed out, not
        names taken, so a member renamed and then failing to write leaves a gap in the
        numbering (``(1)`` unused, next member gets ``(2)``). The result is still
        deterministic, which is what the spec asks. ``_release_claim`` resets the
        counters, so a name freed by an anti-item is found again."""
        parent = requested.parent
        if transformed.type == MemberType.DIRECTORY:
            stem, suffix = requested.name, ""
        else:
            stem, suffix = requested.stem, requested.suffix
        counter_key = collision_key(self._rel_name(dest, requested), self._policy)
        n = self._rename_next.get(counter_key, 1)
        while True:
            candidate = parent / f"{stem} ({n}){suffix}"
            candidate_key = collision_key(self._rel_name(dest, candidate), self._policy)
            if candidate_key not in collision_map and not self._occupied(candidate):
                self._rename_next[counter_key] = n + 1
                return candidate
            n += 1

    def _apply_anti_item(
        self,
        original: ArchiveMember,
        dest_path: Path,
        written_paths: set[Path],
        collision_map: dict[str, _Claim],
        dest: Path,
    ) -> ExtractionResult:
        """Delete what an earlier member of this run wrote at the anti-item's name.

        An exact path this run wrote wins. Otherwise the name is looked up through the
        collision map, so it matches the way every other collision does: under STRICT
        and STANDARD, an anti-item ``readme`` deletes the ``README`` this run wrote,
        which on a case-insensitive filesystem is the file it names. TRUSTED keys on the
        exact name, so there it is exact. Directories are not in the map, so a directory
        matches only by its exact path. Nothing is deleted that this run did not write.
        """
        if dest_path not in written_paths:
            claim = collision_map.get(
                collision_key(self._rel_name(dest, dest_path), self._policy)
            )
            if claim is not None and claim.path in written_paths:
                dest_path = claim.path
        if dest_path not in written_paths:
            return ExtractionResult(
                original, dest_path, ExtractionStatus.EXTRACTED, None
            )
        try:
            st = os.lstat(dest_path)
        except FileNotFoundError:
            written_paths.discard(dest_path)
            self._release_claim(collision_map, dest, dest_path)
            return ExtractionResult(
                original, dest_path, ExtractionStatus.EXTRACTED, None
            )
        if stat.S_ISDIR(st.st_mode):
            os.rmdir(dest_path)
        else:
            os.unlink(dest_path)
        written_paths.discard(dest_path)
        self._release_claim(collision_map, dest, dest_path)
        return ExtractionResult(original, dest_path, ExtractionStatus.EXTRACTED, None)

    def _release_claim(
        self, collision_map: dict[str, _Claim], dest: Path, path: Path
    ) -> None:
        """Drop the collision claim on ``path`` once its content is gone.

        ``written_paths`` and ``collision_map`` both track what this run put on disk, so
        an anti-item delete has to clear both or the map keeps advertising a claim for
        content that no longer exists — which a later same-key member would see as a
        collision, aborting under ``AbortOn.NAME_COLLISION`` against an empty destination
        or revising an already-deleted member to ``OVERWRITTEN``."""
        key = collision_key(self._rel_name(dest, path), self._policy)
        claim = collision_map.get(key)
        if claim is not None and claim.path == path:
            del collision_map[key]
            # A freed name can be the first free one for a later RENAME again.
            self._rename_next.clear()

    def _write_file(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        stream: BinaryIO | None,
        dest_path: Path,
        tracker: BombTracker,
        source_paths: dict[int, list[Path]],
    ) -> ExtractionResult:
        if not self._prepare_destination(transformed, dest_path, atomic=True):
            return ExtractionResult(
                original, None, ExtractionStatus.NOT_OVERWRITTEN, None
            )

        os.makedirs(dest_path.parent, exist_ok=True)
        if stream is None and self._retyped:
            # The pass yielded this member as a link, with no data stream, before its
            # data showed it is a file. Random access opens it now. A forward-only pass
            # is already past that data, so the content is out of reach: fail the member
            # rather than write an empty file for one the archive carries in full.
            reader = self._reader
            if reader is None or reader._streaming:
                raise ExtractionError(
                    f"{quoted(original.name)} is flagged as a link but its data is a "
                    "file's content, which a streaming pass cannot go back for",
                    member_name=original.name,
                )
            with contextlib.closing(reader._lazy_member_stream(original)) as reopened:
                self._write_file_atomic(reopened, dest_path, transformed, tracker)
        else:
            self._write_file_atomic(stream, dest_path, transformed, tracker)

        # Record this FILE's path under the ORIGINAL member id so later hardlinks whose
        # link_target_member is this member can os.link against it.
        source_paths.setdefault(original.member_id, []).append(dest_path)
        return ExtractionResult(original, dest_path, ExtractionStatus.EXTRACTED, None)

    def _write_symlink(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        dest_root: Path,
        dest_path: Path,
    ) -> ExtractionResult:
        target = transformed.link_target
        if target is None and original._link_target_absent:
            # The archive says this is a link and records nowhere for it to point — a
            # 7-Zip-written directory symlink or junction, or a reparse buffer naming
            # nothing. There is nothing to write, and nothing here went wrong, so this
            # is a LINK_TARGET_UNAVAILABLE result rather than a per-member failure that
            # OnError.STOP would turn into an aborted extraction.
            # Checked before _prepare_destination so a member we are not going to
            # write cannot unlink an existing destination under OverwritePolicy.REPLACE.
            return ExtractionResult(
                original, None, ExtractionStatus.LINK_TARGET_UNAVAILABLE, None
            )

        if target is None:
            # Unset for any other reason, which always means the archive records a
            # target this read could not produce. A data-stored target has been looked
            # for by now, either while listing or by `_transform` on request, so it is
            # out of reach: compressed, split across volumes, encrypted, or refused as
            # too long. Reporting that as the status above would claim success while
            # dropping a member the archive describes in full, so it stays the
            # per-member failure it was before that status existed. The reason is the
            # backend's to report: `BaseArchiveReader._emit_link_target_unavailable`.
            raise LinkTargetNotFoundError(
                "Symlink has no target",
                member_name=transformed.name,
            )

        if not self._prepare_destination(transformed, dest_path):
            return ExtractionResult(
                original, None, ExtractionStatus.NOT_OVERWRITTEN, None
            )

        os.makedirs(dest_path.parent, exist_ok=True)
        # A symlink is target-independent: create it even if the target was filtered out,
        # appears later, or lies outside the archive — it may dangle. Only the escape
        # check below constrains it. An os.symlink failure (unsupported FS) propagates as
        # a per-member OnError failure; no copy-the-target fallback.
        os.symlink(target, dest_path)

        # Re-validate the symlink target AFTER creating it, resolving through the real
        # filesystem. check_universal already rejected an absolute or escaping target at
        # planning time, but that check resolves the target lexically against the tree as it
        # looked *then*. The authoritative question — where does this link actually point? —
        # can only be answered against the filesystem as it is now, because an *earlier*
        # extracted member may have planted a symlink on one of this target's path
        # components that redirects it outside dest (a "chained symlink": e.g. member 1 is
        # `sub -> /tmp/evil`, member 2 is `sub/link -> x`, so `sub/link` resolves to
        # `/tmp/evil/x`). Path.resolve() follows those on-disk links, so it catches the
        # escape the planning check cannot see. We can't do this "just before" creating the
        # link because there is no link to resolve until it exists; and resolving the *bare
        # target string* would only repeat check_universal. So: create, resolve, and unlink
        # if it escaped. A cyclic/adversarial link makes resolve() raise ELOOP/RuntimeError,
        # which we also treat as an escape (fail safe rather than crash). This is the third
        # of the three defense-in-depth layers named in the `safe-extraction` spec
        # ("Symlink Escape Re-Validated at Extraction Time"); layers 1-2 are in
        # check_universal.
        try:
            resolved = (dest_path.parent / target).resolve()
            escaped = not (resolved == dest_root or resolved.is_relative_to(dest_root))
        except (OSError, RuntimeError):
            escaped = True
        if escaped:
            try:
                dest_path.unlink()
            except OSError:
                pass
            raise SymlinkEscapeError(
                "Symlink target escapes destination",
                member_name=transformed.name,
                link_target=target,
            )

        return ExtractionResult(original, dest_path, ExtractionStatus.EXTRACTED, None)

    def _write_hardlink(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        dest_path: Path,
        tracker: BombTracker,
        source_paths: dict[int, list[Path]],
        orphans: list[_Orphan],
        forward_only: bool,
        result_index: int,
    ) -> ExtractionResult:
        source = original.link_target_member
        if source is None:
            raise LinkTargetNotFoundError(
                "Hardlink target not found",
                member_name=transformed.name,
                link_target=original.link_target,
            )

        if source.member_id in source_paths:
            if not self._prepare_destination(transformed, dest_path, atomic=True):
                return ExtractionResult(
                    original, None, ExtractionStatus.NOT_OVERWRITTEN, None
                )
            os.makedirs(dest_path.parent, exist_ok=True)
            self._place_link(
                source_paths, source.member_id, dest_path, transformed, tracker
            )
            return ExtractionResult(
                original, dest_path, ExtractionStatus.EXTRACTED, None
            )

        # Source not on disk yet: either it was excluded by the selector/filter, or (in a
        # crafted/non-TAR-ordered archive) it simply appears later in archive order. The
        # second pass distinguishes the two: a source written later in this same pass is
        # just linked against; a truly excluded one is re-read and materialized.
        if forward_only:
            # Forward-only: the source's bytes already streamed past — unrecoverable. Per
            # spec this is a per-member ExtractionError handled by OnError.
            raise ExtractionError(
                "Hardlink source was excluded and cannot be recovered on a "
                "forward-only stream",
                link_target=source.name,
                member_name=transformed.name,
            )
        # Re-readable: resolve in the second pass.
        orphans.append(_Orphan(result_index, original, transformed, dest_path, source))
        return ExtractionResult(original, None, ExtractionStatus.FAILED, None)

    # --- orphan (second pass) ------------------------------------------------------

    def _resolve_orphans(
        self,
        reader: "BaseArchiveReader",
        source_paths: dict[int, list[Path]],
        orphans: list[_Orphan],
        tracker: BombTracker,
        results: list[ExtractionResult],
        collision_map: dict[str, _Claim],
        dest: Path,
    ) -> None:
        orphans_by_source: dict[int, list[_Orphan]] = {}
        for orphan in orphans:
            orphans_by_source.setdefault(orphan.source.member_id, []).append(orphan)

        # A source that was written LATER in the first pass (a link preceding its source in
        # archive order) is already on disk: just link against it — re-reading its bytes
        # here would create an independent inode and double-count against the bomb limits.
        needed: set[int] = set()
        for source_id, group in orphans_by_source.items():
            if source_id in source_paths:
                self._link_orphan_group(
                    group,
                    source_paths,
                    source_id,
                    tracker,
                    results,
                    collision_map,
                    dest,
                )
            else:
                needed.add(source_id)
        if not needed:
            return

        # One second forward pass over the (re-readable) source; write each needed source's
        # content to the first writable link path and os.link the rest. Driven through the
        # public stream_members() with the needed sources as an identity selector, so
        # unneeded members are never surfaced (or opened — the streams are lazy).
        # Built here for the same reason as the first pass's identity selector:
        # these entries are the coordinator's, so an unmatched one is not reported
        # as MEMBER_SELECTOR_UNMATCHED.
        needed_sources = CollectionSelector(
            [orphans_by_source[source_id][0].source for source_id in needed],
            record=False,
        )
        for member, stream in reader.stream_members(needed_sources):
            group = orphans_by_source[member.member_id]
            try:
                self._materialize_orphan_source(
                    member,
                    stream,
                    group,
                    source_paths,
                    tracker,
                    results,
                    collision_map,
                    dest,
                )
            except (_AlwaysStopResourceLimitError, DiagnosticRaisedError):
                raise
            except (ArchiveyError, OSError) as exc:
                # One failed source, N failed links: the fan-out is recorded on the
                # results themselves, so a caller can tell N separate failures from one
                # failure seen N times without joining against a diagnostic.
                self._record_failure_group(results, group, exc)
                if self._stops_on_failure():
                    raise
                logger.warning(
                    "Skipping orphaned hardlink source %r: %s", member.name, exc
                )
            finally:
                self._close(stream)
            needed.discard(member.member_id)
            if not needed:
                break  # every orphaned source is materialized; stop opening members

        # Any orphan whose source never reappeared (should not happen for a re-readable
        # source) is a per-member failure.
        for source_id in needed:
            err = ExtractionError("Hardlink source was not found on the second pass")
            self._record_failure_group(results, orphans_by_source[source_id], err)
            if self._stops_on_failure():
                raise err

    def _record_failure_group(
        self,
        results: list[ExtractionResult],
        group: list[_Orphan],
        error: ArchiveyError | OSError,
    ) -> None:
        """Record one failed hardlink source as N FAILED results sharing a group id.

        The id is opaque and process-local (``uuid4().hex``): callers compare it for
        equality to join a group and nothing more. Both fields are set together, so a
        partial fill can never look like a group."""
        group_id = uuid.uuid4().hex
        group_size = len(group)
        for orphan in group:
            self._revise_result(
                results,
                orphan.result_index,
                ExtractionResult(
                    orphan.original,
                    None,
                    ExtractionStatus.FAILED,
                    error,
                    failure_group_id=group_id,
                    failure_group_size=group_size,
                ),
            )

    @staticmethod
    def _revise_result(
        results: list[ExtractionResult], index: int, new: ExtractionResult
    ) -> None:
        """Overwrite a recorded result, carrying forward first-pass facts it omits.

        The second pass rebuilds a result from scratch, but two fields were decided in
        the first pass (before the member was deferred) and are still true: the
        ``presented_name`` rewrite, and the ``requested_path`` the member asked for. A
        rebuild that does not supply them must not erase them — results are the sole
        record, so a dropped field is a fact lost rather than a fact reported elsewhere.
        """
        prior = results[index]
        if prior.presented_name is not None and new.presented_name is None:
            new = replace(new, presented_name=prior.presented_name)
        if prior.requested_path is not None and new.requested_path is None:
            new = replace(new, requested_path=prior.requested_path)
        results[index] = new

    def _materialize_orphan_source(
        self,
        source_member: ArchiveMember,
        stream: BinaryIO | None,
        group: list[_Orphan],
        source_paths: dict[int, list[Path]],
        tracker: BombTracker,
        results: list[ExtractionResult],
        collision_map: dict[str, _Claim],
        dest: Path,
    ) -> None:
        # Count the recovered source bytes toward the cumulative/ratio guards too.
        tracker.start_member(source_member)

        # The excluded source's content is written to the FIRST link whose destination the
        # OverwritePolicy allows writing (a SKIP over an existing entry moves on to the next
        # link), never to the source's own name — atomically (temp + os.replace), same as a
        # normal FILE, so a failure while re-reading doesn't clobber an existing entry. The
        # link's transformed copy supplies the on-disk mode/timestamps: hardlinks share one
        # inode, so the metadata must be applied to the file that carries the content. Each
        # link's destination is O2-collision-resolved against the map the main pass built
        # (a deferred link's key may have been claimed after it was orphaned).
        writer: _Orphan | None = None
        writer_path: Path | None = None
        writer_prior: _Claim | None = None
        writer_collided: Path | None = None
        remaining: list[_Orphan] = []
        for orphan in group:
            if writer is not None:
                remaining.append(orphan)
                continue
            resolved, prior, collided_with = self._resolve_collision(
                orphan.original,
                orphan.transformed,
                orphan.dest_path,
                collision_map,
                dest,
            )
            if self._prepare_destination(orphan.transformed, resolved, atomic=True):
                writer, writer_path, writer_prior = orphan, resolved, prior
                writer_collided = collided_with
            else:
                self._revise_result(
                    results,
                    orphan.result_index,
                    ExtractionResult(
                        orphan.original,
                        None,
                        ExtractionStatus.NOT_OVERWRITTEN,
                        None,
                        requested_path=orphan.dest_path,
                        collided_with=collided_with,
                    ),
                )
        if writer is None or writer_path is None:
            return  # every link's destination already exists under SKIP: nothing to write

        os.makedirs(writer_path.parent, exist_ok=True)
        self._write_file_atomic(stream, writer_path, writer.transformed, tracker)
        source_paths.setdefault(source_member.member_id, []).append(writer_path)
        result = ExtractionResult(
            writer.original,
            writer_path,
            ExtractionStatus.EXTRACTED,
            None,
            requested_path=(
                writer_path if writer_prior is not None else writer.dest_path
            ),
            collided_with=writer_collided,
        )
        self._revise_result(results, writer.result_index, result)
        if result.status is ExtractionStatus.EXTRACTED:
            self._mark_overwritten(results, writer_prior)
        self._register_collision_key(
            collision_map, dest, writer.transformed, result, writer.result_index
        )
        self._link_orphan_group(
            remaining,
            source_paths,
            source_member.member_id,
            tracker,
            results,
            collision_map,
            dest,
        )

    def _link_orphan_group(
        self,
        group: list[_Orphan],
        source_paths: dict[int, list[Path]],
        source_id: int,
        tracker: BombTracker,
        results: list[ExtractionResult],
        collision_map: dict[str, _Claim],
        dest: Path,
    ) -> None:
        """Link each orphan in ``group`` against the source content already on disk
        (recorded under ``source_id``), applying the OverwritePolicy per link (O2 collisions
        resolved against the map) and recording per-link results; failures follow
        ``OnError``."""
        for orphan in group:
            resolved, prior, collided_with = self._resolve_collision(
                orphan.original,
                orphan.transformed,
                orphan.dest_path,
                collision_map,
                dest,
            )
            try:
                if not self._prepare_destination(
                    orphan.transformed, resolved, atomic=True
                ):
                    self._revise_result(
                        results,
                        orphan.result_index,
                        ExtractionResult(
                            orphan.original,
                            None,
                            ExtractionStatus.NOT_OVERWRITTEN,
                            None,
                            requested_path=orphan.dest_path,
                            collided_with=collided_with,
                        ),
                    )
                    continue
                os.makedirs(resolved.parent, exist_ok=True)
                self._place_link(
                    source_paths, source_id, resolved, orphan.transformed, tracker
                )
            except (_AlwaysStopResourceLimitError, DiagnosticRaisedError):
                raise
            except (ArchiveyError, OSError) as exc:
                self._revise_result(
                    results,
                    orphan.result_index,
                    ExtractionResult(
                        orphan.original,
                        None,
                        ExtractionStatus.FAILED,
                        exc,
                        requested_path=orphan.dest_path,
                        collided_with=collided_with,
                    ),
                )
                if self._stops_on_failure():
                    raise
                # A single link's failure, not a source fan-out: no group id.
                logger.warning("Skipping hardlink %r: %s", orphan.original.name, exc)
                continue
            result = ExtractionResult(
                orphan.original,
                resolved,
                ExtractionStatus.EXTRACTED,
                None,
                requested_path=resolved if prior is not None else orphan.dest_path,
                collided_with=collided_with,
            )
            self._revise_result(results, orphan.result_index, result)
            if result.status is ExtractionStatus.EXTRACTED:
                self._mark_overwritten(results, prior)
            self._register_collision_key(
                collision_map, dest, orphan.transformed, result, orphan.result_index
            )

    def _mark_overwritten(
        self,
        results: list[ExtractionResult],
        prior: _Claim | None,
    ) -> None:
        """Revise a clobbered member's result to OVERWRITTEN: its content is gone.

        Called once the caller knows the earlier member lost its destination — either
        because a later REPLACE landed on it, or because a REPLACE cleared it and then
        failed. Shared by the main pass and the orphan second pass: a deferred hardlink
        can be the member that replaces an earlier write just as a first-pass member
        can."""
        if prior is None or self._overwrite is not OverwritePolicy.REPLACE:
            return
        clobbered = results[prior.result_index]
        if clobbered.status is ExtractionStatus.SUPERSEDED:
            # A superseded copy the filesystem would not remove: it keeps its status.
            return
        results[prior.result_index] = replace(
            clobbered,
            path=None,
            status=ExtractionStatus.OVERWRITTEN,
            requested_path=(
                clobbered.requested_path
                if clobbered.requested_path is not None
                else clobbered.path
            ),
        )
        # The clobbered member was tallied as EXTRACTED when it completed. It is no
        # longer an EXTRACTED result, and ``members_extracted`` is defined as a tally of
        # results — so the progress counter has to follow the revision, or the final
        # report would claim more extracted members than ``results`` contains.
        if clobbered.status is ExtractionStatus.EXTRACTED:
            self._members_extracted -= 1

    # --- filesystem helpers --------------------------------------------------------

    def _ensure_dest_root(self, dest: Path) -> None:
        """Ensure ``dest`` is a directory to extract into, creating it if absent.

        A dest that resolves to a directory — a real directory or a symlink pointing at
        one — is reused, and members land inside the resolved target (``run`` resolves
        ``dest`` before writing). This matches ``tar -C``/``unzip -d`` and leaves the
        caller's symlink in place; the dest root is trusted (unlike archive-internal
        symlinks, which are never written through).

        A dest that exists as anything else — a regular file, a symlink to a file, a
        dangling symlink — is a hard error regardless of ``OverwritePolicy``: we never
        delete it. Extraction is not an invitation to remove a path the caller pointed at
        by mistake (e.g. a CLI given a file argument where a directory was meant).
        """
        if dest.is_dir():  # real directory or symlink resolving to one: reuse / follow
            return
        # ``lexists`` (not ``exists``) so a dangling symlink is caught here rather than
        # surfacing as a raw FileExistsError from ``mkdir`` below.
        if os.path.lexists(dest):
            raise ExtractionError(
                f"Destination exists and is not a directory: {display_path(dest)}"
            )
        dest.mkdir(parents=True, exist_ok=True)

    def _prepare_destination(
        self, member: ArchiveMember, dest_path: Path, *, atomic: bool = False
    ) -> bool:
        """Apply the OverwritePolicy. Returns True to proceed with creation, False to
        skip (SKIP over an existing entry). Raises ExtractionError under ERROR when the
        entry exists. Uses lstat semantics so a dangling symlink counts as existing.

        ``atomic=True`` is used for FILE and HARDLINK writes, which land via
        ``os.replace()`` over the destination (see ``_write_file_atomic`` /
        ``_place_link``): under REPLACE this leaves an existing **file or symlink** in
        place for that atomic swap (so the old data survives until the new entry is fully
        built, and a symlink is replaced, never written through), removing only an
        existing **directory** first — ``os.replace`` cannot overwrite a directory.
        ``atomic=False`` (DIR / SYMLINK) keeps the plain unlink-then-create: a symlink
        must be created at its final name for the escape re-validation's cycle check, and
        a directory cannot be renamed over a file at all."""
        exists = os.path.lexists(dest_path)
        if not exists:
            return True
        if dest_path in self._stale:
            # A superseded copy of this same name that this run wrote: the member
            # replaces it under any policy, as random access would never have written it.
            # Never a directory (see ``_supersede_written_copy``).
            if not atomic:
                dest_path.unlink()
            return True

        # A real directory being (re)created as a directory is fine under any policy.
        if (
            member.type == MemberType.DIRECTORY
            and dest_path.is_dir()
            and not dest_path.is_symlink()
        ):
            return True

        if self._overwrite is OverwritePolicy.ERROR:
            raise ExtractionError(
                f"Destination already exists: {display_path(dest_path)}",
                member_name=member.name,
            )
        if self._overwrite is OverwritePolicy.SKIP:
            return False
        if (
            self._overwrite is OverwritePolicy.REPLACE
            or self._overwrite is OverwritePolicy.RENAME
        ):
            # REPLACE (and RENAME for the residual directory case — non-directory RENAME
            # members are pre-resolved to a free path, so they never reach an existing
            # entry here): never write-through a symlink. For an atomic FILE write,
            # os.replace handles a file/symlink target atomically, so only a real
            # directory must be removed up front. Otherwise unlink a symlink/file (bytes
            # never follow the link) and rmtree a real directory tree.
            if dest_path.is_dir() and not dest_path.is_symlink():
                shutil.rmtree(dest_path)
                self._removed_existing = True
            elif not atomic:
                dest_path.unlink()
                self._removed_existing = True
            return True
        # This arm destroys the existing entry, so a policy nobody taught this chain
        # must not inherit it: an unknown member stops here.
        assert_never(self._overwrite)

    def _write_file_atomic(
        self,
        stream: BinaryIO | None,
        dest_path: Path,
        member: ArchiveMember,
        tracker: BombTracker | None,
    ) -> None:
        """Write a FILE by streaming into a temp sibling, applying ``member``'s metadata,
        then ``os.replace()``-ing it onto ``dest_path`` — atomic, so a mid-stream failure
        never truncates or removes an existing destination (only the temp is discarded),
        and the target name never appears half-written. The temp lives in the destination
        directory so the rename stays on one filesystem. When the orphan pass materializes
        an unselected hardlink source, ``member`` is the *link's* transformed copy, so the
        file carrying the content gets the link's (policy-capped) mode, not the source's.

        Under TRUSTED, a member with no stored mode gets the mode an ordinary file
        creation would, ``0o666`` less the umask, rather than ``mkstemp``'s private
        ``0o600``. Nothing chmods it afterwards, so the temp's creation mode is the file's
        final mode. STRICT and STANDARD give a mode-less member their own default instead
        (see ``_effective_mode``), so this arm is TRUSTED's alone."""
        if self._effective_mode(member) is None:
            tmp = self._temp_sibling(dest_path.parent)
            fd = _open_new_file(tmp, 0o666)
        else:
            # mkstemp hands back an already-open fd; write straight into it (no
            # close+reopen). Its 0o600 keeps the content private until the chmod below.
            fd, tmp_name = tempfile.mkstemp(dir=dest_path.parent, prefix=_TMP_PREFIX)
            tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as dst:
                self._copy_to_fileobj(
                    stream, dst, tracker, emit_progress=self._emit_progress
                )
            self._apply_metadata(tmp, member)
            os.replace(tmp, dest_path)
        except BaseException:
            # os.replace consumes the temp on success; on any earlier failure remove it so
            # no .archivey-tmp-* file is left behind (the existing destination is untouched).
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    @staticmethod
    def _copy_to_fileobj(
        stream: BinaryIO | None,
        dst: BinaryIO,
        tracker: BombTracker | None,
        emit_progress: Callable[[], None] | None = None,
    ) -> None:
        """Copy ``stream`` into the already-open ``dst`` in chunks, counting each toward the
        bomb tracker. Cleanup of a partial ``dst`` on failure is the caller's job.

        When ``emit_progress`` is set, it is invoked after each full ``_CHUNK`` so callers
        can report intra-member progress (~1 callback/MiB). Short final chunks are left to
        the coordinator's terminal per-member report so sub-chunk members keep a single
        callback. Pass ``None`` (the default) for the zero-cost path.
        """
        if stream is None:
            # A FILE reaching the writer with no data stream is a backend bug, not a valid
            # empty file (a zero-byte FILE still yields a real, empty stream). Silently
            # writing an empty file here would mask that bug, so raise instead. Both FILE
            # write paths (the main pass and the orphaned-source second pass) funnel through
            # here, so this one guard covers them; it surfaces as a per-member failure via
            # the coordinator's OnError handling, attributed to the member being written.
            raise ExtractionError(
                "FILE member has no data stream to extract (backend returned stream=None)"
            )
        while True:
            chunk = stream.read(_CHUNK)
            if not chunk:
                break
            if tracker is not None:
                tracker.count(len(chunk))
            dst.write(chunk)
            # Full chunks only: the terminal report covers the final (possibly short) chunk
            # so a sub-chunk member still gets exactly one callback.
            if emit_progress is not None and len(chunk) == _CHUNK:
                emit_progress()

    def _place_link(
        self,
        source_paths: dict[int, list[Path]],
        source_id: int,
        new_path: Path,
        member: ArchiveMember,
        tracker: BombTracker,
    ) -> None:
        """Create ``new_path`` as a hardlink to the source's content, trying each recorded
        on-disk path in turn; on all-cross-device (EXDEV), copy from an existing path.
        Appends ``new_path`` so a later same-device link can reuse it — which is what
        keeps a fan-out across one device boundary to a single copy per device rather
        than one per link.

        The copy is a real write of the source's full size, so it goes through
        ``tracker`` and counts toward ``max_extracted_bytes``; a link adds no bytes.
        It is written into a new file, not ``shutil.copy2``'d, so it carries this
        member's metadata alone, never the source file's mode.

        The link is built at a temp sibling and ``os.replace``d into place, the same way
        a FILE write lands: ``os.link`` needs a free name, so the alternative is to
        unlink an existing destination first and leave a hole if the link then fails.
        ``os.replace`` moves the link itself and never follows the entry it replaces, so
        a destination symlink is replaced rather than written through."""
        existing = source_paths[source_id]
        tmp = self._temp_sibling(new_path.parent)
        try:
            copied = False
            for candidate in existing:
                try:
                    os.link(candidate, tmp)
                    break
                except OSError as exc:
                    if exc.errno == errno.EXDEV:
                        continue
                    raise
            else:
                # Every recorded path is cross-device: fall back to a copy from the first.
                # Created private when a mode follows (as mkstemp would), at the ordinary
                # creation mode when none does, the same as a FILE write.
                create_mode = (
                    0o600 if self._effective_mode(member) is not None else 0o666
                )
                with (
                    open(existing[0], "rb") as src,
                    os.fdopen(_open_new_file(tmp, create_mode), "wb") as dst,
                ):
                    while chunk := src.read(_CHUNK):
                        tracker.count_copy(len(chunk))
                        dst.write(chunk)
                copied = True
            if copied:
                # Applied before the swap, so the final name never appears with the
                # source's metadata instead of this member's.
                self._apply_metadata(tmp, member)
            os.replace(tmp, new_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        existing.append(new_path)

    @staticmethod
    def _temp_sibling(parent: Path) -> Path:
        """A free ``.archivey-tmp-*`` name in ``parent`` for an atomic swap.

        Unlike ``_write_file_atomic`` this cannot use ``mkstemp``: ``os.link`` needs a
        name that does *not* exist. The destination is trusted at rest (see the threat
        model), and ``os.link`` / ``os.replace`` fail loudly on a name that appeared
        underneath us, so a uuid4 name is enough."""
        while True:
            candidate = parent / f"{_TMP_PREFIX}{uuid.uuid4().hex}"
            if not os.path.lexists(candidate):
                return candidate

    def _effective_mode(self, member: ArchiveMember) -> int | None:
        """The mode to give what ``member`` writes; ``None`` means the creation default.

        The policy transforms fill in a missing mode, but a user filter runs after them
        and may hand back ``mode=None``. STRICT and STANDARD still owe their documented
        default then (file ``0o644``, dir ``0o755``), not whatever the umask leaves.
        Only TRUSTED means "as stored", and a member that stored nothing gets the
        creation default.
        """
        if member.mode is not None or self._policy is ExtractionPolicy.TRUSTED:
            return member.mode
        return 0o755 if member.is_dir else 0o644

    def _apply_metadata(self, path: Path, member: ArchiveMember) -> None:
        """Best-effort ownership / mode / mtime. Failures are swallowed (best-effort).

        Ownership goes first: Linux ``chown`` clears setuid/setgid on a non-directory
        even when root calls it, so a ``chmod`` before it would lose exactly the bits
        TRUSTED promises to keep. GNU tar orders the two the same way for this reason.
        """
        # Ownership only under TRUSTED as root (STRICT/STANDARD never chown).
        if (
            self._policy is ExtractionPolicy.TRUSTED
            and member.uid is not None
            and member.gid is not None
            and hasattr(os, "geteuid")
            and os.geteuid() == 0
        ):
            try:
                os.chown(path, member.uid, member.gid)
            except OSError:
                pass
        mode = self._effective_mode(member)
        if mode is not None:
            try:
                os.chmod(path, mode)
            except OSError:
                pass
        if member.modified is not None:
            try:
                ts = member.modified.timestamp()
                os.utime(path, (ts, ts))
            except (OSError, ValueError, OverflowError):
                pass

    # --- progress / misc -----------------------------------------------------------

    def _estimate_total_bytes(
        self, all_members: list[ArchiveMember] | None
    ) -> int | None:
        if all_members is None:
            return None
        if any(m.is_file and m.size is None for m in all_members):
            return None
        return sum(m.size or 0 for m in all_members if m.is_file)

    def _report_progress(
        self,
        member: ArchiveMember,
        tracker: BombTracker,
        total_estimate: int | None,
        members_done: int,
        members_total: int | None,
        *,
        member_bytes_written: int = 0,
        members_extracted: int = 0,
        members_blocked: int = 0,
    ) -> None:
        if self._on_progress is None:
            return
        self._on_progress(
            ExtractionProgress(
                member=member,
                bytes_written=tracker.total_bytes,
                total_bytes_estimated=total_estimate,
                members_done=members_done,
                members_total=members_total,
                member_bytes_written=member_bytes_written,
                members_extracted=members_extracted,
                members_blocked=members_blocked,
            )
        )

    @staticmethod
    def _close(stream: BinaryIO | None) -> None:
        """Close a member stream once its result is recorded.

        Best-effort: the member's result already stands, and every content verdict fired
        from a read (ADR 0014), so a close failure has no result left to change. An
        interrupt still propagates.
        """
        if stream is not None:
            try:
                stream.close()
            except Exception:  # noqa: BLE001 - teardown hygiene; the result is recorded
                pass
