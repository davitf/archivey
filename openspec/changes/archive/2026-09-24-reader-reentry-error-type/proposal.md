# A callback re-entering its reader is refused with ArchiveyUsageError

## Why

Four places said a diagnostic callback that calls back into the reader that emitted the
diagnostic gets `UnsupportedOperationError`. The code raises `ArchiveyUsageError`, and
has done so since the reader's operation gate diagnosed same-thread re-entry.
`reader-concurrency` already says `ArchiveyUsageError` for the same case, and
`UnsupportedOperationError`'s own docstring routes caller misuse to it. The two
exception trees are disjoint, so a caller who wrote `except UnsupportedOperationError`
from those rows caught nothing.

## What changes

- `diagnostics`: re-entry is refused by the reader's gate with `ArchiveyUsageError`. The
  collector's own guard still raises `UnsupportedOperationError` for a re-entrant call
  that gets far enough to emit a diagnostic of its own.
- `diagnostics`: the scenario row for a callback starting an operation on the same
  reader names `ArchiveyUsageError`.
- `archive-reading`: the `SHALL` in "Explicit configuration object" gets the same split
  as `diagnostics`, and the diagnostics matrix row names `ArchiveyUsageError`.

## Impact

Spec text only. No behaviour changes; the rows now describe what the code does.
