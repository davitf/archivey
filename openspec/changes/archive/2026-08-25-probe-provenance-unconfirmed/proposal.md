# The unconfirmed-format signal should follow provenance, not confidence

## Why

`brotli-probe-framing-gate` (#261) built an honesty channel: when a single-file result was
identified **only** by a content probe and the read later fails, the error is stamped
`format_unconfirmed=True`, its message names the unconfirmed identification, and
`PROBE_FORMAT_UNCONFIRMED` is emitted. That was the right thing to build. It is wired to
the wrong switch.

The trigger is `FormatProvenance.probe_guess`, which is
`detected_by == "content_probe" and confidence is GUESS` (`src/archivey/core.py:112`).
Confidence is a *detection-time* claim about strength of evidence; whether a caller was
told "the format may have been wrong" is a *provenance* question — was there anything
beyond a probe agreeing? Those coincide for Brotli's uncompressed-first class and nowhere
else.

Measured on 66 361 real files with `scripts/exploration/probe_residual_census.py`:

| claimed as | confidence | genuine | fabricated | stamped on failure? |
| --- | --- | --- | --- | --- |
| Brotli | `GUESS` | 0 | 60 | yes |
| Brotli | `PROBABLE` (compressed-first) | 4 | 64 | **no** |
| LZMA Alone | `PROBABLE` (unconditional) | 0 | 4 | **no** |

**68 of 128 fabricated probe claims — 53% — carry no unconfirmed signal at all.** A caller
gets `CorruptionError (format=LZMA_ALONE)` with `format_unconfirmed=False`: an error that
blames the data and vouches for a format identification that was one probe's guess.

Reproduced end-to-end on `main` (`bee7735`): a thin little-endian Mach-O stub in front of a
real 7z archive detects as `LZMA_ALONE` / `PROBABLE` / `content_probe`, lists one
fabricated `sfx_macho.bin.uncompressed` member, and fails with `CorruptionError` carrying
`format_unconfirmed=False`. This is the same silent-wrong-answer shape
`sfx-format-detection` and `brotli-probe-framing-gate` each closed a slice of — still open,
wearing a different format label.

## What Changes

- **Key the stamp on provenance.** A single-file result whose format came from a content
  probe with **no corroborating evidence** — no exact magic, no matching extension — SHALL
  stamp `format_unconfirmed` on a decode failure, whatever confidence detection reported.
  `probe_guess` becomes `probe_only` and stops consulting `DetectionConfidence`.
- **Stop the message naming GUESS.** `_emit_unconfirmed_format` currently says "identified
  only by a content probe at GUESS confidence". Once the trigger is provenance, the
  confidence word is wrong and misleading.
- **Inner-TAR corroboration counts** (`brotli-probe-framing-gate` task 5.9). When
  `_resolve_single_file_or_tar` upgrades a probe hit to `TAR_BROTLI` because it found
  `ustar` in the decompressed prefix, that is a second independent signal — as good as an
  extension agreeing. Such a result is corroborated: it reports `PROBABLE` and does not
  stamp.
- **Re-examine one decision, with data.** `brotli-probe-framing-gate` task 3.1a kept
  `PROBABLE` for a probe-only Brotli hit whose first meta-block is compressed, on the
  strength of 0.014% acceptance **on random data**. Real files are not random: that class
  is 64 fabrications against 4 genuine streams on this tree. See `design.md` — the
  provenance change makes it largely moot, which is the cleanest way to resolve it.

## Impact

- Modules: `src/archivey/internal/format_provenance.py` (the field and its docstring),
  `src/archivey/core.py` (`_format_provenance`), `src/archivey/internal/base_reader.py`
  (`_mark_format_unconfirmed`, `_emit_unconfirmed_format` message text),
  `src/archivey/internal/detection.py` (inner-TAR corroboration).
- Public API: no signature changes. More failures carry `format_unconfirmed=True` and more
  readers emit `PROBE_FORMAT_UNCONFIRMED`. Under `pedantic()` with `RAISE`, that means
  some reads that previously raised a plain `CorruptionError` now raise the same type with
  the flag set and the diagnostic escalated — same type, more information.
- Tests: the existing provenance tests key on Brotli `GUESS`; they need a peer case
  (LZMA Alone probe-only) and a compressed-first Brotli case that now stamps.
- Docs: `docs/formats.md` gained a sentence in #261 describing the `GUESS` rule; it moves
  to provenance.
- Related: narrows `dev-docs/open-issues.md` P12 / `dev-docs/threat-model.md` O10 on the
  *reporting* axis. It does not reduce the false-positive count — that is
  `probe-completeness-gate`'s job — it makes the survivors honest.

## Capabilities

### New Capabilities

### Modified Capabilities

- `error-handling` — the unconfirmed-format stamp keys on probe-only provenance rather
  than on `GUESS` confidence.
- `format-detection` — inner-TAR corroboration is a corroborating signal; and the
  confidence rules stop being load-bearing for error behaviour.

## Decisions

- **Provenance, not confidence.** Confidence answers "how strong is this evidence"; the
  stamp answers "was there any evidence besides one probe". Conflating them is what
  produced a 53% blind spot. Keeping them separate also frees `DetectionConfidence` to be
  tuned later without silently changing which errors are stamped.
- **Corroboration is the test, not probe identity.** The rule does not enumerate probes.
  "A probe said so and nothing else did" is the condition, so a probe added tomorrow is
  covered without an edit.
- **Not a refusal.** As in #261: a probe-only result that reads cleanly stays a success.
  This change adds information to failures, never new failures.
