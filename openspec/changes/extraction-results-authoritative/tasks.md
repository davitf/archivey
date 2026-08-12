## 1. Ceiling rules and doctrine cleanup

- [x] 1.1 Add the admission + placement clauses to `openspec/specs/diagnostics/spec.md`
      (via the delta), so a proposed code has a written test to fail
- [x] 1.2 Retire the O-23 sentence in `review/docs/observations.md` — replace
      "diagnostics are archive-related, not usage-related" with a pointer to the
      ceiling, noting the archive/usage split is descriptive only
- [x] 1.3 Cross-reference the discussion doc and the three reviewer opinions in
      `dev-docs/discussions/` from the decision record

## 2. `ExtractionResult` becomes authoritative

- [x] 2.1 Add `ExtractionStatus.OVERWRITTEN` (`"overwritten"`) in
      `internal/extraction_types.py`
- [x] 2.2 Add `presented_name`, `failure_group_id`, `failure_group_size` to
      `ExtractionResult` (appended, defaulting to `None`)
- [x] 2.3 Change `collision_map` to carry the prior writer's result index
      (`extraction.py:337`, `_register_collision_key` at `:701`), so the earlier
      member's result can be revised
- [x] 2.4 On a `REPLACE`-resolved collision, revise the earlier member's result to
      `OVERWRITTEN` with `path=None`, keeping `requested_path`; leave result ordering
      in member-processing order
- [x] 2.5 Populate `presented_name` at the portable-rewrite site
      (`_transform`, `extraction.py:543-546`) with the pre-rewrite full name
- [x] 2.6 Move hardlink failure-group metadata from `ExtractionOutcomeContext` onto the
      `FAILED` results

## 3. Extraction leaves the diagnostics channel

- [x] 3.1 Remove `EXTRACTION_MEMBER_BLOCKED`, `EXTRACTION_MEMBER_FAILED`,
      `EXTRACTION_NAME_COLLISION`, `EXTRACTION_NAME_SANITIZED` from `DiagnosticCode`
- [x] 3.2 Remove `ExtractionOutcomeContext`, `NameCollisionContext`,
      `NameSanitizedContext` and their entries in the context union / validators
- [x] 3.3 Delete the emission sites in `internal/extraction.py` (`_emit_collision`,
      `_emit_name_sanitized`, and the blocked/failed outcome emissions), keeping the
      result-side records from §2
- [x] 3.4 Confirm `ExtractionReport.diagnostics` still carries reading diagnostics
      observed during extraction (timestamps, symlinks, digests, rewinds)

## 4. `abort_on` opt-in

- [x] 4.1 Add `AbortOn` to `internal/extraction_types.py` and export it
- [x] 4.2 Add `NameCollisionError` / `NameRewrittenError` (`ExtractionError`
      subclasses) to `exceptions.py`
- [x] 4.3 Thread `abort_on: Collection[AbortOn] = ()` through `extract()`,
      `extract_all()` and the coordinator
- [x] 4.4 Implement immediate abort: remove the triggering member's partial output,
      process no later member, return no report
- [x] 4.5 Fix the `OnError` docstring (`extraction_types.py:83`) that calls
      abort-on-blocked "a separate future opt-in" — it now exists and is named

## 5. Policy presets

- [x] 5.1 Add `ARCHIVE_INTEGRITY_CODES` and the `DiagnosticPolicy.strict()` /
      `.pedantic()` constructors in `diagnostics.py`
- [x] 5.2 Assert a preset equals the equivalent hand-built policy (no new axis)

## 6. CLI and docs

- [x] 6.1 Add the `OVERWRITTEN` label to `cli/extract_cmd.py`; surface
      `presented_name` where the CLI reports rewritten names
- [x] 6.2 Update `docs/errors-and-diagnostics.md` (four codes gone; presets; the
      taxonomy-growth note), `docs/safe-extraction.md` and `docs/extracting.md`
      (`abort_on`, the new status, the corrected "future opt-in" wording)
- [x] 6.5 Document `AbortOn.NAME_SANITIZED` as a narrow escape hatch for callers who
      refuse any rewritten on-disk name — never as part of ordinary strict extraction.
      Point auditing callers at `presented_name` instead
- [x] 6.3 Grep `docs/` for the four removed code names
- [x] 6.4 Decided: **`--abort-on EVENT` ships on the CLI** (repeatable;
      `blocked-member` / `name-collision` / `name-sanitized`). Library-only would have
      left the wedge gap `VISION` warns about — the CLI is the first consumer, and
      fail-closed extraction of an untrusted archive is exactly a CLI use. The existing
      stop-path handler already prints the error and exits 1, so an abort needed no new
      CLI reporting.

## 7. Tests

- [x] 7.1 Replace the BLOCKED/FAILED diagnostic-pairing assertions across the
      extraction/diagnostic suites with result-side assertions
- [x] 7.2 Convert `test_raise_disposition_stops_despite_continue` into an
      `abort_on={AbortOn.BLOCKED_MEMBER}` test — same behaviour, now named
- [x] 7.3 Lock the `REPLACE` collision case: `A.txt` + `a.txt`, first result
      `OVERWRITTEN` with `path=None`, second `EXTRACTED` at that path (this is
      the case that is silent today)
- [x] 7.4 Cover the three-spelling case: caller `filter` rename followed by a
      portable rewrite yields distinct `member.name` / `presented_name` / `path.name`
- [x] 7.5 Cover `abort_on` for each member, including that `TRUSTED` produces no
      collision event and so no abort
- [x] 7.6 Cover the presets, including `strict()` not raising on `EMPTY_ARCHIVE`
      or `PASSWORD_ARGUMENT_UNUSED`
- [x] 7.7 `uv run --no-sync pytest`; `ruff format` / `ruff check`;
      `uv run --no-sync pyrefly check` and `uv run --no-sync ty check`

## 9. Follow-ups found in review (PR #235)

- [x] 9.1 `members_extracted` follows a result revised to `OVERWRITTEN` (progress
      defines the tally as counting `EXTRACTED` *results*)
- [x] 9.2 `_revise_result` carries `requested_path` forward, not just `presented_name`
- [x] 9.3 CLI prints full relative names on both sides of the `name rewritten:` arrow
- [x] 9.4 `dev-docs/threat-model.md` and `review/docs/outline.md` repointed off the
      removed `EXTRACTION_NAME_*` diagnostics
- [x] 9.5 Maintainer decision: a `REPLACE` that clears a this-run destination and then
      fails revises the earlier member to `OVERWRITTEN`. HARDLINK writes made atomic
      (temp link + `os.replace`) so the window closes for that member type; SYMLINK
      keeps unlink-then-create because the escape check's cycle detection needs the link
      at its final name, and DIRECTORY-over-file cannot be renamed at all. `Overwrite
      Policy` MODIFIED to match
- [x] 9.7 Maintainer decision: an anti-item delete releases the collision claim, so the
      map cannot advertise content this run already removed. `Anti-item extraction`
      MODIFIED to say the claim and the on-disk entry are cleared together
- [x] 9.6 Tests for the failure-group fields, the failed-clobber cases, hardlink
      atomicity, and the CLI cases that had none

## 8. Verify

- [x] 8.1 `openspec validate --strict extraction-results-authoritative`
- [x] 8.2 Gate before archive: `rg 'EXTRACTION_MEMBER_|EXTRACTION_NAME_|ExtractionOutcomeContext|NameCollisionContext|NameSanitizedContext' openspec/specs/`
      Run after syncing. **One intended hit remains**: the `diagnostics` clause that
      names the three context classes to say they SHALL NOT exist — a prohibition, not
      a stale reference. Everything else was cleared; the one genuinely stale mention
      (a "has it on `ExtractionOutcomeContext` today" migration note in
      `safe-extraction`) was reworded in both the delta and the synced spec.
- [x] 8.3 Sync main specs from this change's deltas (`diagnostics`, `safe-extraction`)
