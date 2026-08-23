## 0. Sequencing — read before writing any code

- [ ] 0.1 **This change's `error-handling` delta is written on top of `brotli-probe-framing-gate`'s unarchived text, not on `openspec/specs/error-handling/spec.md`.** That change is implemented (#261, `bee7735`) but deliberately **not archived** (its D7 → A), so the live spec still lacks `format_unconfirmed` entirely and lacks the *decode failure on probe-only evidence* requirement this one MODIFIES. Archive `brotli-probe-framing-gate` **before** archiving this change, then verify the merged requirements still carry: the `format_unconfirmed` attribute row, the four-step failure contract, the no-refusal clause, and the "message must not claim zero output" rule
- [ ] 0.2 **Land `probe-completeness-gate` first and re-run the census.** It removes 91 of the 128 measured fabrications, including 57 of the 64 compressed-first ones. The argument here survives unchanged — the blind spot is structural, not a matter of count — but every number quoted in a PR description should be the post-completeness one. Re-run `scripts/exploration/probe_residual_census.py`
- [ ] 0.3 This change **closes** `brotli-probe-framing-gate` tasks **5.8** (provenance-based `format_unconfirmed`) and **5.9** (inner-TAR corroboration). Check those boxes when this lands rather than keeping two records
- [ ] 0.4 The `format-detection` deltas here are **ADDED** requirements, so they do not collide with `probe-completeness-gate`'s (also ADDED, different requirements) or with `prefixed-archive-detection`'s (MODIFIED, different requirements). Those three archive in any order relative to each other

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
