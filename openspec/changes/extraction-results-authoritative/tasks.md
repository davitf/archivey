## 1. Ceiling rules and doctrine cleanup

- [ ] 1.1 Add the admission + placement clauses to `openspec/specs/diagnostics/spec.md`
      (via the delta), so a proposed code has a written test to fail
- [ ] 1.2 Retire the O-23 sentence in `review/docs/observations.md` — replace
      "diagnostics are archive-related, not usage-related" with a pointer to the
      ceiling, noting the archive/usage split is descriptive only
- [ ] 1.3 Cross-reference the discussion doc and the three reviewer opinions in
      `dev-docs/discussions/` from the decision record

## 2. `ExtractionResult` becomes authoritative

- [ ] 2.1 Add `ExtractionStatus.OVERWRITTEN` (`"overwritten"`) in
      `internal/extraction_types.py`
- [ ] 2.2 Add `presented_name`, `failure_group_id`, `failure_group_size` to
      `ExtractionResult` (appended, defaulting to `None`)
- [ ] 2.3 Change `collision_map` to carry the prior writer's result index
      (`extraction.py:337`, `_register_collision_key` at `:701`), so the earlier
      member's result can be revised
- [ ] 2.4 On a `REPLACE`-resolved collision, revise the earlier member's result to
      `OVERWRITTEN` with `path=None`, keeping `requested_path`; leave result ordering
      in member-processing order
- [ ] 2.5 Populate `presented_name` at the portable-rewrite site
      (`_transform`, `extraction.py:543-546`) with the pre-rewrite full name
- [ ] 2.6 Move hardlink failure-group metadata from `ExtractionOutcomeContext` onto the
      `FAILED` results

## 3. Extraction leaves the diagnostics channel

- [ ] 3.1 Remove `EXTRACTION_MEMBER_BLOCKED`, `EXTRACTION_MEMBER_FAILED`,
      `EXTRACTION_NAME_COLLISION`, `EXTRACTION_NAME_SANITIZED` from `DiagnosticCode`
- [ ] 3.2 Remove `ExtractionOutcomeContext`, `NameCollisionContext`,
      `NameSanitizedContext` and their entries in the context union / validators
- [ ] 3.3 Delete the emission sites in `internal/extraction.py` (`_emit_collision`,
      `_emit_name_sanitized`, and the blocked/failed outcome emissions), keeping the
      result-side records from §2
- [ ] 3.4 Confirm `ExtractionReport.diagnostics` still carries reading diagnostics
      observed during extraction (timestamps, symlinks, digests, rewinds)

## 4. `abort_on` opt-in

- [ ] 4.1 Add `AbortOn` to `internal/extraction_types.py` and export it
- [ ] 4.2 Add `NameCollisionError` / `NameRewrittenError` (`ExtractionError`
      subclasses) to `exceptions.py`
- [ ] 4.3 Thread `abort_on: Collection[AbortOn] = ()` through `extract()`,
      `extract_all()` and the coordinator
- [ ] 4.4 Implement immediate abort: remove the triggering member's partial output,
      process no later member, return no report
- [ ] 4.5 Fix the `OnError` docstring (`extraction_types.py:83`) that calls
      abort-on-blocked "a separate future opt-in" — it now exists and is named

## 5. Policy presets

- [ ] 5.1 Add `ARCHIVE_INTEGRITY_CODES` and the `DiagnosticPolicy.strict()` /
      `.pedantic()` constructors in `diagnostics.py`
- [ ] 5.2 Assert a preset equals the equivalent hand-built policy (no new axis)

## 6. CLI and docs

- [ ] 6.1 Add the `OVERWRITTEN` label to `cli/extract_cmd.py`; surface
      `presented_name` where the CLI reports rewritten names
- [ ] 6.2 Update `docs/errors-and-diagnostics.md` (four codes gone; presets; the
      taxonomy-growth note), `docs/safe-extraction.md` and `docs/extracting.md`
      (`abort_on`, the new status, the corrected "future opt-in" wording)
- [ ] 6.3 Grep `docs/` and `openspec/specs/` for the four removed code names

## 7. Tests

- [ ] 7.1 Replace the BLOCKED/FAILED diagnostic-pairing assertions across the
      extraction/diagnostic suites with result-side assertions
- [ ] 7.2 Convert `test_raise_disposition_stops_despite_continue` into an
      `abort_on={AbortOn.BLOCKED_MEMBER}` test — same behaviour, now named
- [ ] 7.3 Lock the `REPLACE` collision case: `A.txt` + `a.txt`, first result
      `OVERWRITTEN` with `path=None`, second `EXTRACTED` at that path (this is
      the case that is silent today)
- [ ] 7.4 Cover the three-spelling case: caller `filter` rename followed by a
      portable rewrite yields distinct `member.name` / `presented_name` / `path.name`
- [ ] 7.5 Cover `abort_on` for each member, including that `TRUSTED` produces no
      collision event and so no abort
- [ ] 7.6 Cover the presets, including `strict()` not raising on `EMPTY_ARCHIVE`
      or `PASSWORD_ARGUMENT_UNUSED`
- [ ] 7.7 `uv run --no-sync pytest`; `ruff format` / `ruff check`;
      `uv run --no-sync pyrefly check` and `uv run --no-sync ty check`

## 8. Verify

- [ ] 8.1 `openspec validate --strict extraction-results-authoritative`
- [ ] 8.2 Sync main specs from this change's deltas (`diagnostics`, `safe-extraction`)
