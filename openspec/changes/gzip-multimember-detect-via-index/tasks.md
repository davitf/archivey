# Tasks — multi-member gzip detection via rapidgzip's index

> **BLOCKED — spike resolved NO (see `FINDINGS.md`).** rapidgzip 0.16.0 does not expose gzip
> member boundaries through its index; task 1.3's stop-condition is met. The implementation tasks
> below are retained for the record but are **not** to be executed. Do not sync the delta spec.
> Read `design.md` + `FINDINGS.md` first.

## 1. Confirm rapidgzip exposes member boundaries — DONE (NO)

- [x] 1.1 `block_offsets()` / `available_block_offsets()` expose random-access **seek points**,
      not gzip member starts: member boundaries never appear in the offsets at any
      parallelization (serial gives only `{start, EOS}`). `export_index` serializes the same seek
      index; no member/stream-count accessor exists. Evidence: `FINDINGS.md`.
- [x] 1.2 The index is complete after a read to EOF (`block_offsets_complete() == True`), but
      "complete" means the seek index — it carries no member-boundary data.
- [x] 1.3 Member boundaries are **not derivable** → STOP. Byte scan stays; this change is a
      documented no-op. Knock-on: the deferred per-member ISIZE **sum** is equally blocked (it
      needed the same member data). Maintainer decision: close/shelve this change (see FINDINGS).

## 2. Implement the index query

- [ ] 2.1 Add an accessor from `_GzipTruncationCheckStream` to the wrapped accelerator handle
      (no dependency leak beyond the codec layer).
- [ ] 2.2 Replace `_has_additional_gzip_member` with "index reports ≥2 members?"; keep
      `gzip_has_additional_member` as the fallback when the index is unavailable.
- [ ] 2.3 Preserve the conservative direction: on the ambiguous truncated-mid-second-member case,
      fall back to "further magic ⇒ do not raise" (never false-positive on a valid file).

## 3. Tests

- [ ] 3.1 Valid 2- and 3-member gzip via rapidgzip → no `TruncatedError`, and the byte scan is
      **not** invoked (spy/counter).
- [ ] 3.2 Truncated single-member → `TruncatedError` from the index, no whole-file scan.
- [ ] 3.3 Truncated mid-second-member → conservative fallback; valid sibling never false-flagged.
- [ ] 3.4 Index-unavailable → byte-scan fallback; behavior identical to today.
- [ ] 3.5 `uv run pyrefly check` + `uv run ty check` clean; `uv run ruff format`; full suite in
      `[all]`, `[all-lowest]`, `[core-only]`.

## 4. OpenSpec

- [ ] 4.1 `openspec validate --strict gzip-multimember-detect-via-index` green.
- [ ] 4.2 Note the follow-on: the deferred per-member ISIZE **sum**
      (`rapidgzip-truncation-investigation`) should build on this index accessor.
- [ ] 4.3 Do **not** accept / sync this delta until §1 confirms rapidgzip exposes gzip *member*
      boundaries (else the "SHALL prefer the index" contract asserts a capability the code lacks —
      keep the index-first wording in this change folder only until then).
- [ ] 4.4 Sync into main `seekable-decompressor-streams` when landing. This and
      `gzip-truncation-backstop-any-seekable` `MODIFY` the **same** requirement with divergent
      full texts; OpenSpec `MODIFIED` replaces the whole requirement, so an independent sync
      clobbers whichever lands second. Hand-author **one merged requirement text** covering both
      the any-seekable scope and the index-based disambiguation.
