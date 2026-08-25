## 0. Sequencing — read before writing any code

- [x] 0.1 ~~Archive `brotli-probe-framing-gate` before archiving this change.~~ — **done**: archived as `openspec/changes/archive/2026-08-23-brotli-probe-framing-gate` and its deltas synced into `openspec/specs/`. This change's `error-handling` delta is therefore written against **shipped** text. Verified after the sync that the merged requirements carry all four things this delta builds on: the `format_unconfirmed` attribute row, the four-step failure contract, the no-refusal clause, and the "message must not claim zero output" rule
- [ ] 0.2 **Land `probe-completeness-gate` first and re-run the census.** It removes 91 of the 128 measured fabrications, including 57 of the 64 compressed-first ones. The argument here survives unchanged — the blind spot is structural, not a matter of count — but every number quoted in a PR description should be the post-completeness one. Re-run `scripts/exploration/probe_residual_census.py`
- [ ] 0.3 This change **closes** `brotli-probe-framing-gate` tasks **5.8** (provenance-based `format_unconfirmed`) and **5.9** (inner-TAR corroboration). Check those boxes when this lands rather than keeping two records
- [ ] 0.4 Archive-order independence, rechecked per delta. The `format-detection` deltas here are **ADDED**, so they do not collide with `probe-completeness-gate`'s (also ADDED, different requirements) or with `prefixed-archive-detection`'s (MODIFIED, different requirements). This change also carries **MODIFIED** deltas for `error-handling` (*Every ArchiveyError carries standard attributes*, *A decode failure on probe-only evidence names its provenance*) and `diagnostics` (*Immutable diagnostic values…*) — neither sibling change touches either capability, so all three still archive in any order relative to each other. Recheck this if a sibling grows a `diagnostics` delta

## 1. Move the trigger to provenance

- [ ] 1.1 Rename `FormatProvenance.probe_guess` → `probe_only` and rewrite its docstring: the field records that a probe was the *sole* evidence, not that confidence was `GUESS`. The rename is load-bearing — the present name is why the call site reads as correct
- [ ] 1.2 In `src/archivey/core.py` `_format_provenance`, drop the `confidence is DetectionConfidence.GUESS` clause. The condition becomes `detected_by == "content_probe"` **and** nothing corroborated it
- [ ] 1.3 Decide where "nothing corroborated it" is computed. `FormatInfo` does not currently record whether the extension agreed — `detect_format` knows it at the time (`ext_match`) and throws it away. Either carry it on `FormatInfo` or recompute at the call site; carrying it is preferable, since recomputing duplicates the extension-matching rules
- [ ] 1.4 Treat the inner-TAR upgrade as corroboration (task 3.x), so a `TAR_BROTLI` result reached via `_resolve_single_file_or_tar` is not probe-only
- [ ] 1.5 Check `EXTENSION_FORMAT_UNCONFIRMED` still keys on `detected_by="extension"` and does not start double-reporting alongside `PROBE_FORMAT_UNCONFIRMED`

## 2. Message and diagnostic

- [ ] 2.1 `_emit_unconfirmed_format` (`base_reader.py`) currently writes "identified only by a content probe at **GUESS confidence**". Drop the confidence clause — it will be wrong for most stamped errors after 1.2
- [ ] 2.2 `_mark_format_unconfirmed`'s rewritten message already avoids claiming zero output ("Partial output may already have been produced"). Keep that; do not regress it while editing
- [ ] 2.3 Keep the `escalate_as` machinery intact: under `pedantic()` with `RAISE`, the typed `TruncatedError`/`CorruptionError` must survive rather than becoming `DiagnosticRaisedError`. That logic is subtle and already commented — extend the comment if the condition moves
- [ ] 2.4 Keep the once-per-reader emit (`_probe_unconfirmed_emitted`) and its reasoning about deduplication versus escalation
- [ ] 2.5 **The `diagnostics` delta must archive alongside the `error-handling` one.** The live *Immutable diagnostic values…* requirement independently documents the trigger as `detected_by="content_probe"` **at `GUESS` confidence**, so changing only `error-handling` would leave the two specs contradicting each other about when `PROBE_FORMAT_UNCONFIRMED` fires — the same silent drift #262 had to repair by hand when rebuilding a broken `diagnostics` MODIFIED for the framing-gate archive. `specs/diagnostics/spec.md` restates that requirement whole with the confidence clause replaced by provenance and three scenario rows changed; verify by diffing against live before archiving
- [ ] 2.6 `PROBE_FORMAT_UNCONFIRMED` stays **out** of `ARCHIVE_INTEGRITY_CODES` (the `strict` set). That exclusion is documented in *A probe-only decode failure is filterable as unconfirmed* and is independent of this change: it exists so an escalating emit surfaces the typed error via `escalate_as` rather than `DiagnosticRaisedError`. Do not "tidy" it in while moving the trigger

## 3. Inner-TAR corroboration (was `brotli-probe-framing-gate` 5.9)

- [ ] 3.1 When `_resolve_single_file_or_tar` upgrades a `content_probe` hit to a `TAR_*` format, report `PROBABLE` — not the `GUESS` the underlying probe class would have given
- [ ] 3.2 Mark that result corroborated so it does not stamp `format_unconfirmed`
- [ ] 3.3 Do not extend this to the SFX or magic paths; they never reach this requirement

## 4. Verify

- [ ] 4.1 Red–green from the reproduction: a thin little-endian Mach-O stub in front of a real 7z detects as `LZMA_ALONE` / `PROBABLE` / `content_probe` on `bee7735`, lists one fabricated `*.uncompressed` member, and fails with `CorruptionError` carrying **`format_unconfirmed=False`**. After this change the flag is `True` and `PROBE_FORMAT_UNCONFIRMED` is emitted. Assert the flag and the diagnostic, not just the exception type
- [ ] 4.2 Probe-only **LZMA Alone** failure stamps, at `PROBABLE`
- [ ] 4.3 Probe-only **compressed-first Brotli** failure stamps, at `PROBABLE`
- [ ] 4.4 Corroborated cases do **not** stamp: `.br` extension; `TAR_BROTLI` via inner-TAR; exact magic
- [ ] 4.5 A probe-only result that reads **cleanly** stays a success — no error, no diagnostic, no downgrade. This change must add no new failures
- [ ] 4.6 `pedantic()` with `RAISE` on a probe-only failure still produces the typed error with the flag, not `DiagnosticRaisedError`
- [ ] 4.7 Confidence values are unchanged by this change — pin the existing `GUESS`/`PROBABLE` matrix so a future reader cannot mistake this for a confidence retune
- [ ] 4.8 `./scripts/test.sh --all-configs`
- [ ] 4.9 `openspec validate --strict probe-provenance-unconfirmed`
- [ ] 4.10 Update `docs/formats.md`, whose sentence from #261 describes the rule as confidence-keyed
- [ ] 4.11 Re-run `scripts/exploration/probe_residual_census.py` and record the stamped/unstamped split in `dev-docs/investigations/brotli-content-probe-results.md`. The target is zero fabrications with no signal

## 5. Follow-ups (explicitly not in this change)

- [ ] 5.1 Whether `FormatInfo` should expose corroboration as a public field rather than an internal provenance detail. Task 1.3 may make it internal; a caller wanting to know "was this identification corroborated?" before reading has no way to ask
- [ ] 5.2 `brotli-probe-framing-gate` task 3.1a's compressed-first `PROBABLE` split stands on its random-data measurement, but that number does not describe real files (64 fabrications against 4 genuine streams on the measured tree). This change makes the split harmless — it no longer steers error behaviour — so retuning it is now a pure confidence question, worth revisiting only with a corpus of real extensionless Brotli streams
