## Why

A reader asks its backend for the member list more than once, and the backends that build
`ArchiveMember` objects on demand (ZIP, ISO) hand back a second set of objects for the same
members. Measured on `main` (`c45ce34`), counting `_iter_members()` calls on a two-member
archive of each format:

| Calls made | ZIP | ISO | 7z | RAR |
| --- | --- | --- | --- | --- |
| `members()` twice, then iterate | 1 | 1 | 1 | 1 |
| `members_report_if_available()` twice, then `members()` | 3 | 3 | 3 | 3 |
| `extract_all()` | 2 | 2 | 2 | 2 |

The base reader does cache the resolved list (`_materialized`). What it does not cache is the
index-only list behind `members_report_if_available()`, and `extract_all` calls that peek
before it materializes. Two more paths walk the backend on their own: the streaming pass
(`_ProgressivePassIterator`) and the 7z/RAR `_iter_with_data` overrides, which iterate the
backend's private `self._members` and skip the base's registration entirely. That last one
is a live bug: on a fresh reader, `stream_members()` over a 7z or a solid RAR yields members
whose public `member_id` raises `AttributeError` ("member not yet registered"), in both
access modes. ZIP, ISO and non-solid RAR stamp ids `0, 1, …` as expected. 7z and RAR only
look cached because `_iter_members` is `yield from self._members`, a list they build at
open.

The second set of objects is why PR 386 needed a per-position diagnostic ledger
(`_report_member_diagnostic`, `_member_reports`, `reattach_to_member`) and why
`_register_member` carries `_presentation_checked` and an "already stamped, re-account"
branch. ISO still has the defect (`dev-docs/known-issues.md`, "ISO counts a member's
typing-time diagnostics once per listing pass"). `dev-docs/IDEAS.md` and
`dev-docs/code-map.md` both record that nothing decided the second set of objects: it
falls out of the index-only result not being stored. ADR 0007 already settled the other
half: members are mutable, filled in place, and previously returned members are live
objects.

## What Changes

- The base reader owns **one member list per reader**, filled by **one** `_iter_members()`
  walk. Each member is stamped, checked and counted exactly once, when the walk yields it.
- Every consumer reads that list: the index-only peek, `members()` / `scan_members()` /
  `members_report()`, `__iter__`, `stream_members`, `extract_all`, and the 7z/RAR data
  passes. A consumer that reaches the end of what has been walked pulls the next member
  from the walk.
- Link resolution completes the **same objects** the peek already returned, in place.
  Repeated peeks return the same objects.
- Removed: the per-position diagnostic ledger, `_presentation_checked`, the
  stamped-member re-accounting branch, the listing tracker's reset-and-recount per pass,
  and `DiagnosticCollector.reattach_to_member`. ZIP's typing-time emits go back to direct
  emits. The ISO known issue closes without its planned fix.
- 7z and RAR keep their private lists for folder grouping and solid-prefix sums; the base
  stops depending on them for listing.
- Streaming `extract_all()` over a ZIP with a duplicate name stops aborting. Today the
  pass yields objects whose `is_current` is not stamped until EOF, so the shadowed entry
  is written and the later one fails with `ExtractionError: Destination already exists`.
  Random access on the same archive reports `SUPERSEDED` (design D1a).
- Typing-time diagnostics carry the member's `member_id` on every backend. Only ZIP does
  today; 7z, RAR and ISO report `None` (design D8).

Not changing: the public API, `ArchiveMember`'s shape, the complete-or-raise contract and
listing limits. When a pass resolves links changes only for 7z and solid RAR, whose passes
start registering and finalizing like every other backend's (design D6). When
`is_current` settles changes only where a peek completes the walk before a streaming
pass reaches EOF (design D1a).

## Capabilities

### Modified Capabilities

- `archive-reading` — adds the one-listing-per-reader requirement (object identity across
  listing methods, exact per-member diagnostic counts).
- `access-mode-and-cost` — the report peek returns the same member objects the resolved
  list will, with data-stored link fields filled in place later.

## Impact

- `src/archivey/internal/base_reader.py`: the listing paths collapse onto one walk
  (`_materialize_members`, `_get_members_index_only`, `_begin_forward_pass` /
  `_ProgressivePassIterator`, `_register_member`, `_report_member_diagnostic`).
- `src/archivey/internal/backends/`: `zip_reader.py` emits directly again;
  `sevenzip_reader.py` and `rar_reader.py` data passes iterate the base list.
- `src/archivey/internal/diagnostics_collector.py`: `reattach_to_member` removed.
- Docs: `dev-docs/known-issues.md` (ISO entry resolved), `dev-docs/IDEAS.md` (entry
  removed), `dev-docs/code-map.md` ("Listing can happen twice" bullet rewritten).
