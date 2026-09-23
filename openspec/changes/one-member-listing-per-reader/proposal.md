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
- Listing a solid 7z with symlinks stops re-decoding the folder once per link. Real 7-Zip
  and py7zr output puts link data mid-folder, compressed with the files (measured in
  design D6a). Today `members()` decodes 273 KB to read four link targets of 43 bytes
  from a 111 KB folder. Each folder is decoded at most once for its links, up to its last
  link. A streaming pass reads link bytes from its own decode, including links a consumer
  never reads past (design D6b).
- New reader setting `ArchiveyConfig.read_link_targets` (default `True`), proposed and
  ruled by davitf. ZIP, 7z and RAR3/4 store a symlink's target as member data. With
  `True`, those targets are read in both modes, including links a `stream_members`
  selector excluded, so the report matches random access. On ZIP and 7z that can
  decompress unselected data and consult the password provider. With `False`, listing and
  `stream_members()` read no member data for a link target, nothing prompts, and the
  laziness promise holds exactly. `extract_all` still reads the targets of links it writes,
  but only after its selector and filter have seen them with `link_target=None`. Header-carried targets (RAR5, TAR, ISO) are unaffected (design D6c).
- Typing-time diagnostics carry the member's `member_id` on every backend. Only ZIP does
  today; 7z, RAR and ISO report `None` (design D8).

Not changing: the public API apart from the new `read_link_targets` setting,
`ArchiveMember`'s shape, the complete-or-raise contract and listing limits. When a pass resolves links changes only for 7z and solid RAR, whose passes
start registering and finalizing like every other backend's (design D6). When
`is_current` settles changes only where a peek completes the walk before a streaming
pass reaches EOF (design D1a).

## Capabilities

### Modified Capabilities

- `archive-reading` — adds four requirements. Each member is listed once per reader:
  object identity across listing methods, exact per-member diagnostic counts, and
  `member_id` on typing-time diagnostic contexts on every backend. Last-entry-wins
  `is_current` is stamped once, when the walk completes or stops on terminal damage. A
  streaming pass finalizes on its own cursor, not on walk completion. The
  `stream_members` laziness requirement gains a symlink-target exception, governed by the
  new `read_link_targets` setting, which gets its own requirement (D6c).
- `access-mode-and-cost` — the report peek returns the same member objects the resolved
  list will, with data-stored link fields filled in place later.
- `format-7z` — a new requirement bounds 7z link reads to one decode per folder, up to its
  last link member, in both modes. It refines "Stream solid folders with bounded memory".

## Impact

- `src/archivey/internal/base_reader.py`: the listing paths collapse onto one walk
  (`_materialize_members`, `_get_members_index_only`, `_begin_forward_pass` /
  `_ProgressivePassIterator`, `_register_member`, `_report_member_diagnostic`).
- `src/archivey/internal/backends/`:
  - `zip_reader.py` emits directly again.
  - `sevenzip_reader.py`: the data pass iterates the base list. Link targets are read
    once per folder in random access, and from the pass's own decode when streaming.
  - `rar_reader.py`: the solid data pass iterates the base list.
  - `iso_reader.py` passes the listing position into typing-time diagnostics. This closes
    its known issue.
- `src/archivey/internal/diagnostics_collector.py`: `reattach_to_member` removed.
- `src/archivey/config.py`: `ArchiveyConfig.read_link_targets`, honoured by the ZIP, 7z
  and RAR `_ensure_link_target`.
- Tests: a new small 7-Zip `-snl` fixture with symlinks before, between and after file
  members in one solid folder, plus its non-solid twin.
- Docs: `dev-docs/known-issues.md` (ISO entry resolved), `dev-docs/IDEAS.md` (entry
  removed), `dev-docs/code-map.md` ("Listing can happen twice" bullet rewritten).
