## Context

The base reader has four ways into a backend's `_iter_members()`, and only one of them
stores what it gets back:

| Path | Entry | Stores the list? | Registers members? |
| --- | --- | --- | --- |
| Resolved materialization | `_materialize_members` (`base_reader.py:1140`) | Yes, in `_materialized` | Yes |
| Index-only peek | `_get_members_index_only` (`:1225`), behind `members_report_if_available()` | No: a new walk on every call until materialized | Yes, re-stamping |
| Streaming forward pass | `_begin_forward_pass` → `_ProgressivePassIterator` (`:2358`) | Yes, at EOF, via `_finalize_pass_links` | Yes |
| 7z / solid-RAR data pass | `iter(self._members)` in `sevenzip_reader.py:474`, `rar_reader.py` solid branch | No | **No** |

`extract_all` peeks (`extraction.py:331`) and then materializes (`:343`), so every backend
with an upfront index is walked twice per extraction. ZIP and ISO build new `ArchiveMember`
objects on each walk. Everything downstream that keys on "this member" then has to use the
listing position instead of the object: `_presentation_checked` for the bidi check, and PR
386's `_member_reports` ledger with `reattach_to_member` for typing-time diagnostics. The
listing tracker resets and recounts on every walk for the same reason.

### What earlier decisions say

- **ADR 0007** (mutable `ArchiveMember` filled in place) and the `archive-data-model`
  requirement it backs: members are mutable, the library is their only writer, late fields
  (sizes, link targets, diagnostics) are filled in place on the object the caller already
  holds, and "previously returned members are live objects, not point-in-time snapshots".
  This change applies that rule to one more place, the peek.
- **`archive-reading`, "Sequential in-order iteration"** already requires a completed
  streaming pass to be "finalized in place on already-yielded objects". A streaming reader
  already hands out members before resolving their links and completes them later. The
  random-access peek will do the same.
- **`dev-docs/IDEAS.md`** ("Reuse the index-only pass's members instead of rebuilding them")
  and **`dev-docs/code-map.md`** ("Listing can happen twice…") both say the second set of
  objects was never decided. It falls out of the index-only result not being stored. Both
  flag the listing tracker and the concurrency gate as the places needing care, and so
  does this design.
- **`reader-concurrency`, "Materialization boundary"**: the resolved report and name index
  are built privately and published once as immutable containers, while `ArchiveMember`
  objects keep backend-populated late-bound fields. This design keeps the resolved report's
  containers private until published. The member objects inside may already have been
  handed out by the peek, which the boundary allows because they are not the containers.

Nothing found argues for fresh objects per call.

## Goals / Non-Goals

**Goals**

- `_iter_members()` runs once per reader for a walk that completes, whichever public
  methods are called and in whatever order.
- One `ArchiveMember` object per member per reader, the same across every listing method,
  every pass and every peek.
- Registration (id stamp, presentation checks, listing accounting) runs once per member.
- Delete the per-position dedupe machinery the double walk made necessary.

**Non-Goals**

- Changing when a random-access pass resolves links. The default `_iter_with_data` still
  materializes first, and ZIP symlink targets are still read at materialization.
- Removing 7z's or RAR's private `self._members`. They use it for folder grouping
  (`_folder_members`), solid-prefix sums and `info.member_count`, and may keep building it
  at open.
- Any public API change.

## Decisions

### D1. One walk, owned by the base: an append-only list plus the unfinished iterator

The base holds `_listed: list[ArchiveMember]`, the name index for it, and
`_walk: Iterator[ArchiveMember] | None` (the one `_iter_members()` generator), plus the
walk's outcome once it ends (`done`, or the terminal `CorruptionError` / `TruncatedError`).
A single private method pulls the next member. It stamps `_member_id` / `_archive_id`,
runs the presentation checks, accounts the member with the listing tracker, indexes its
name and appends it.

Everything reads the list by position and pulls when it reaches the end:

| Consumer | Reads |
| --- | --- |
| Index-only peek | Pulls to the end, then applies last-entry-wins `is_current` if not yet applied, and returns the list. Reads no member data. |
| Resolved materialization | Pulls to the end, resolves links on the listed objects, publishes `_materialized` |
| Streaming forward pass | Pulls one member at a time, yields it, and finalizes at the end as today |
| 7z / solid-RAR data pass | Iterates the listed members by position (D6) |

**Rejected: cache the peek's result and leave the other paths alone.** That is the smallest
diff and fixes ZIP and ISO for `extract_all`. But the streaming pass and the 7z/RAR passes
would still walk on their own, the member-id bug would remain, and the dedupe ledger could
not be deleted, because a streaming peek followed by the pass would still produce two
object sets.

### D2. Failure: discard what nobody saw, keep what somebody did

- **Random access.** No random-access consumer hands out a member before the walk has
  ended. The peek, `members()` and `__iter__` all drain first. So a walk that fails with
  anything other than terminal damage (`ResourceLimitError`, `KeyboardInterrupt`,
  `MemoryError`) leaves a prefix nobody holds. It is discarded along with the tracker's
  counts, and a later call starts a fresh walk, exactly as `_materialize_members` does
  today.
- **Streaming.** The pass has already yielded the prefix, so it cannot be discarded. A
  failed walk poisons the reader as `_ProgressivePassIterator` does today ("reopen the
  archive to retry").
- **Terminal damage** (`CorruptionError` / `TruncatedError`) is a completed walk with an
  error. The prefix and the error are stored once. `members_report()` and the peek return
  the incomplete report; `members()`, `scan_members()` and `get()` raise, as the spec
  already requires.

One small behaviour change follows. Today an upfront-index peek whose walk hits terminal
damage propagates the error. After this change it returns the stored incomplete report,
which is the shape the peek already returns after an incomplete pass. The walks that can
fail this way are rare: ZIP's `infolist()` and 7z's header are parsed at open, so the
per-member typing step is what would have to fail.

### D3. Link resolution completes the listed objects in place

Resolution runs on `_listed`, and the objects the peek returned are the ones that gain
`link_target`, `link_target_member` and link diagnostics. `_resolve_link_target`'s
per-member `_link_target_resolved` memo already makes resolution idempotent. A resolution
step that fails part-way leaves the members it finished resolved, and a retry resolves the
rest. It does not re-walk.

For a caller, this means a member held from a peek later shows its link fields filled.
That is the ADR 0007 contract ("live objects, not point-in-time snapshots"), already true
of streaming passes. The spec delta states it for the peek.

### D4. Listing limits: account once, enforce per caller

The tracker accounts each member once, when it is pulled. There is no more
`reset()` / re-account per pass, and the "already stamped: re-account" branch in
`_register_member` is deleted.

Enforcement stays per caller, the way `_progressive_enforce_listing_limits` works today:

- A pull made on behalf of an enforcing caller (`members()`, `scan_members()`,
  `members_report()`, the peek, `extract_all`'s prep) raises `ResourceLimitError` as soon
  as a limit is crossed. A ZIP bomb is still refused part-way through the listing, before
  the rest of its members are built.
- A pull made for `stream_members()` / `__iter__` accounts without enforcing, since
  iteration stays the unguarded escape hatch.
- An enforcing caller that finds members already pulled by a non-enforcing one runs
  `assert_within_limits()` on the running totals before returning, as the cached-report
  paths do today.

### D5. Concurrency: the walk has one owner at a time

Pulling from the walk is reader-wide work of the same kind as first-touch materialization.
Under `CONCURRENT` it goes through the same election: one owner walks, others wait on the
condition variable and then read the list. Without `CONCURRENT`, overlap raises
`ArchiveyUsageError` as today. `reader-concurrency` already names
"`members_report_if_available` initialization" as single-owner, and this design makes the
code match. No lock is held while the backend yields or while diagnostics callbacks run,
so the "no reader-state lock held during scan and callbacks" rule stands.

A streaming reader is single-owner by construction. A peek from inside a streaming
`for` loop runs while the pass generator is suspended between yields, so nobody is
mid-pull. For an upfront-index backend, the peek then drains the walk ahead of the pass.
This is safe because walking an upfront index reads no member data (the definition of
`_MEMBER_LIST_UPFRONT`). For other backends the peek returns `None` and pulls nothing.

### D6. 7z and solid-RAR data passes iterate the base list

`sevenzip_reader._iter_with_data` and the solid branch of `rar_reader._iter_with_data`
iterate the base list instead of `self._members`. In random access they drain the walk
without resolving links first, which matches today's timing: neither reads link data
before the pass. In streaming they pull through the shared forward pass, so members are
stamped and registered and the pass finalizes at EOF like TAR's.

This fixes the unregistered-member bug in the proposal. It has one consequence to
measure, not assume: finalizing a streaming 7z or solid-RAR pass at EOF runs
`_resolve_link_target` on its symlinks, and 7z stores link targets in member data. If that
re-decodes a solid folder, the pass must capture the target while it streams the member
(the member's own data is the target) rather than re-read at EOF. Task 4.4 measures this
before choosing.

### D7. What is deleted

- `_member_reports`, the memo half of `_report_member_diagnostic`, and
  `DiagnosticCollector.reattach_to_member`. What remains of `_report_member_diagnostic` is
  an attaching `emit` with a `diagnostic_logger` override. Inline it into its callers, or
  keep it as that thin wrapper if `archivey.normalization` routing reads better that way.
- `_presentation_checked`; the bidi check runs once because registration does.
- `_register_member`'s "already stamped" branch.
- `_get_members_index_only`, `_pass_scanned` / `_pass_by_name_lists` as separate state from
  the materialized list, and the tracker `reset()` calls in `_begin_forward_pass` /
  `_materialize_members`.
- The `report_key` plumbing (`report_key=index` at ZIP's and 7z's typing-time emits, and
  the `index` parameter ZIP's `_to_member` gained for it), where nothing but the ledger uses
  the position.
- `_iter_members`' docstring about separate enumerations having to agree. Order stability
  is still worth stating, because a fresh random-access walk after a discarded failure
  (D2) must produce the same ids.

## Risks / Trade-offs

- **Peeked members change later.** A caller that peeks, holds a symlink member, then calls
  `members()` sees `link_target` go from `None` to a value on the object it holds. That is
  the documented live-object contract, and today's behaviour on streaming readers. The
  spec delta makes it explicit for the peek.
- **Concurrency regression surface.** The walk now shares the materialization election.
  The existing `tests/test_concurrent_multithread.py` and `test_concurrent_cooperative.py` cover first-touch. A new case covers
  a peek racing `members()` under `CONCURRENT`.
- **Error-path drift.** Three error behaviours (discard, poison, store-incomplete) now live
  on one object instead of three. Each gets a test that fails if it is routed to the
  wrong one.
