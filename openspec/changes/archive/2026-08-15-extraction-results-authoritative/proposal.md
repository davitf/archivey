## Why

The diagnostics taxonomy has an inclusion floor ("no advisory SHALL be log-only")
and no ceiling. The only written admission rule — *"diagnostics are archive-related,
not usage-related"* (`review/docs/observations.md`, O-23) — was contradicted by the
taxonomy on the day it was written, and three separate reviews have since rederived
the same unwritten test. `dev-docs/discussions/diagnostics-archive-vs-usage.md` and
the three reviewer responses circulated the question; all three reject the
archive-vs-usage cut, and all three converge on a knowability ceiling.

The reviews also isolated a real defect the ceiling alone does not fix. Extraction is
the one operation that **returns a structured per-item report**, and it reports the
same facts twice: `ExtractionReport.results` carries per-member `status`/`error`, and
the diagnostics channel re-reports blocked and failed members. Verified against the
tree: the duplication is normative (`safe-extraction` requires one matching diagnostic
per continued `BLOCKED`/`FAILED` result), and a `REPLACE` collision is genuinely
silent in `results` — both members report a plain `EXTRACTED` at the same path, with
the diagnostic carrying the only labelled record of which one lost.

Two facts therefore have no result-side home today, and two attempts to give them one
hit occupied slots: `SUPERSEDED` already means *non-current duplicate* (decided from
`is_current` at listing time), and `requested_path != path` is already the defined
`OverwritePolicy.RENAME` marker.

## What Changes

- **Write the missing ceiling** into `diagnostics`, as two clauses rather than one: an
  **admission** rule (report only what the caller could not determine from the declared
  contract and can act on) and a **placement** rule (when an operation returns a
  structured per-item report, that report is the sole carrier of per-item outcomes).
- **Extraction leaves the diagnostics channel.** Remove `EXTRACTION_MEMBER_BLOCKED`,
  `EXTRACTION_MEMBER_FAILED`, `EXTRACTION_NAME_COLLISION`, `EXTRACTION_NAME_SANITIZED`
  and their `ExtractionOutcomeContext` / `NameCollisionContext` / `NameSanitizedContext`
  variants. 22 codes → 18.
- **Relocate every fact into `ExtractionResult`**, with new signals rather than reused
  ones: `ExtractionStatus.OVERWRITTEN` for the member a later `REPLACE` clobbered
  (revised retroactively, `path=None`), `presented_name` for a portable rewrite, and
  `failure_group_id` / `failure_group_size` for hardlink fan-out.
- **Preserve escalation with a named opt-in.** Add `abort_on: Collection[AbortOn]` to
  `extract()` / `extract_all()`. `AbortOn.BLOCKED_MEMBER` is the fail-closed
  "abort on the first unsafe member" that `OnError`'s docstring has promised as a
  "separate future opt-in" — and that `RAISE` on `EXTRACTION_MEMBER_BLOCKED`
  implements today by accident. `NAME_COLLISION` / `NAME_SANITIZED` replace the
  escalation lost when those facts leave the diagnostics channel.
- **Add named policy presets.** `DiagnosticPolicy.strict()` (raise on the
  archive-integrity set) and `.pedantic()` (raise on everything), with
  `ARCHIVE_INTEGRITY_CODES` exported. Document that new codes may appear in minor
  releases, so a bare `default=RAISE` is not version-stable and the presets are the
  documented strict mode.
- **Retire the O-23 sentence** so in-flight work stops citing it as settled.
- **Close the failed-clobber hole in `OverwritePolicy.REPLACE`.** A `REPLACE` that
  clears a this-run destination and then fails leaves the earlier member's content gone
  while its result still reads `EXTRACTED` — the same class of dishonesty this change
  exists to remove. That result is now revised to `OVERWRITTEN`. HARDLINK writes also
  become atomic (temp link + `os.replace`, as FILE writes already were), which removes
  the window entirely for that member type; SYMLINK and DIRECTORY cannot be staged and
  keep unlink-then-create.

## Capabilities

### Modified Capabilities

- `diagnostics` — four codes and three context variants removed; admission + placement
  ceiling; named presets and the taxonomy-growth contract
- `safe-extraction` — `ExtractionStatus.OVERWRITTEN`; `presented_name` and
  failure-group fields on `ExtractionResult`; `abort_on` opt-in; collision and
  sanitize recorded in results rather than diagnostics; atomic HARDLINK replacement and
  the failed-clobber revision in `Overwrite Policy`

## Impact

- **Breaking** public API: four `DiagnosticCode` members and three context dataclasses
  removed; `ExtractionStatus` gains a member (a caller exhaustively matching statuses
  must handle it). Pre-1.0, no aliases. All of it is tag-gated and wants to land before
  `0.2.0`.
- Modules: `diagnostics.py` (codes, contexts, presets), `internal/extraction_types.py`
  (`ExtractionStatus`, `ExtractionResult`, `AbortOn`), `internal/extraction.py`
  (collision map carries the prior writer's result index; emission sites become result
  revisions), `exceptions.py` (`NameCollisionError`, `NameRewrittenError`),
  `cli/extract_cmd.py` (new status label).
- Docs: `docs/errors-and-diagnostics.md`, `docs/safe-extraction.md`,
  `docs/extracting.md` (the `OnError` "future opt-in" wording is now wrong).
- Tests: the extraction/diagnostic suites assert the pairing that this change removes;
  `test_raise_disposition_stops_despite_continue` locks the accidental abort behaviour
  and becomes an `abort_on` test.
- **Pre-existing drift, folded in:** `MEMBER_NAME_ENCODING_INFERRED` had no row in the
  `diagnostics` spec's context table, though the code, `NameEncodingContext`, the
  kind-map entry and the ZIP emission site all ship. The row is added here rather than
  deferred, because this change names the code in `ARCHIVE_INTEGRITY_CODES` and would
  otherwise ship a preset contradicting its own "closed" table. Adding it records
  shipped reality; it does not pick a winner between competing designs, so
  `AGENTS.md`'s pause-and-ask rule is satisfied by the disclosure rather than by
  deferral.
