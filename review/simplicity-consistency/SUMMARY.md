# Simplicity & consistency — SUMMARY

**Headline.** After `#225`, the uniform-interface surface is mostly honest:
directory `format=` is loud, seekable-stream gzip CRC works, solid password
confirm is deferred, duplicate `is_current` is cross-format, and codec install
hints point at `[recommended]`. What remains before `0.2.0` is a **small set of
accidents** (raw `ValueError` on empty volumes, silent `encoding=` discard,
ZIP underlying-close → `CorruptionError`, Path-gated `compressed_size`) plus
**vocabulary / docs / one usage-side diagnostic** decisions — not a second debt
ledger.

**Baseline:** `2792f9c` (post-#225 / #227). `unrar` + `7z` on `PATH`; every
`list_supported_formats()` entry is `FormatSupport.FULL`. pytest **2132 passed /
65 skipped / 3 deselected** (`[all]`); ruff / pyrefly / ty clean; `openspec
validate --all` 25/25. Evidence: `parity-matrix.md`, `repro/probe_parity_matrix.py`,
`repro/repros.py`, `tests/test_guardrails.py`.

**Method:** expected column from VISION + landed specs → observed probe over
corpus × backends × Path/BytesIO/pipe → O-21 deepen on disagreements. Seeds ≈ half
the budget; matrix + silent-knob sweep found F1/F2 beyond the seed list.

---

## Top findings (severity × confidence)

| ID | Sev | Conf | Class | Where | Disposition / vehicle |
|---|---|---|---|---|---|
| F1 | High | CONFIRMED | Accident | `volumes.py` → `open_archive([])` / non-seekable volume streams | **Bug fix** → `ArchiveyUsageError` / `StreamNotSeekableError` |
| F2 | High | CONFIRMED | Accident | `encoding=` on 7z/RAR/dir/ISO/single-file silently discarded | **Bug fix or product** — reject like passwords, or document |
| F3 | Med | CONFIRMED | Accident | ZIP underlying `ZipFile` closed while reader live → `CorruptionError` via blanket ValueError map | **Bug fix** — map "already closed" → `ArchiveyUsageError` |
| F4 | Med | CONFIRMED | Spec fiction | `testing-contract` RTL "warns **or rejects**"; code only `logger.warning` | **Spec change** — drop "or rejects" |
| F5 | Med | CONFIRMED | Awkward | `STREAM_REWIND_REDECOMPRESSES` describes **caller seek**, not archive | **Decide** (O-23) — keep / demote / split |
| F6 | Med | CONFIRMED | Residual Path gate | single-file `compressed_size` only when `isinstance(Path)` | **Bug fix** — `SEEK_END` on seekable streams |
| F7 | Low | CONFIRMED | Docs stale | `must-explain` #25 still says directory `format=` overruled | **Docs-only** |
| F8 | Low | CONFIRMED | Vocabulary | `seekable_members` vs `open_stream(seekable=)` | **Product** freeze question |
| F9 | Low | CONFIRMED | Product | CLI overwrite/`OnError`/dest ≠ library | **Accept** — cli-product Q1 |
| F10 | Low | PLAUSIBLE | Leak | `rar_unrar` `RuntimeError("unrar produced no stdout pipe")` | **Bug fix** (defensive) |
| F14 | Low | CONFIRMED | Concept tax | gotchas ~7 / opening ~11 format-conditionals; 29 must-explain | **Signal** — not a bug ID to fix alone |
| F15 | Low | CONFIRMED | Format law | Header-encrypted 7z/RAR password at **open**; data encrypt stays lazy | **Accept + docs** caveat |
| F16 | Low | PLAUSIBLE | Config no-op | format-scoped config knobs on other formats | **Accept or document** |

**F3 nuance:** normal `reader.close()` then `open`/`members` already raises
`ArchiveyUsageError` (`#225`). The accident is only the **underlying ZipFile
closed while the reader is still live** path (`r._archive.close()` → public
`open` → `CorruptionError`).

---

## What is actually fine

- **Directory `format=`** — rejected loudly (`#225`).
- **Password on non-encrypting formats** — central `SUPPORTS_PASSWORD` reject.
- **Seekable BinaryIO gzip CRC** — filled after `#225`; residual is **compressed_size** (F6).
- **lzip digest path** — seekable-gated, not Path-gated.
- **Solid 7z/RAR folder password confirm** — deferred (`#225` / O-26).
- **Install hints** — `[recommended]` / `[seekable]`; no live `[7z]` leftover (F11).
- **Duplicate `is_current`** — `_apply_last_entry_wins_is_current` + RAR `path;n` (F12).
- **Pass-driver** — single `_drive_pass_streams`; no post-`#184` third copy (F13).
- **CLI → `internal/`** — only `ExtractionProgress` spelling; type is public.
- **Pipe refusals** — loud `StreamNotSeekableError`; format-law split matches expected column.
- **Solid random-open cost** — silent by design; discover via `access_cost`.
- **Cost receipts** — listing/access axes match format law on the probed corpus.
- **Close-on-reader-close** — settled (`#225`); reader-level API is typed.
- **Seeds that were non-issues:** Path gates other than F6; extras naming; pass-driver regression; duplicate-name residual after P1.

---

## Concept count (caller-facing simplicity)

| Page | Format-conditionals (heuristic) |
|---|---:|
| `docs/gotchas.md` | ~7 |
| `docs/opening-and-listing.md` | ~11 |
| `docs/reading-members.md` | ~1 |
| `must-explain.md` | 29 items |

Consistency-flavoured must-explain IDs: **#4, #9–11, #13, #16, #21, #23, #25**.
Paying F1/F2/F3/F6 deletes future Gotchas; F8/F9 are vocabulary/product.

TAR streaming vs RA final-header honesty remains **format-law residual**
(open-issues P3) — suite already asserts it; not a new accident.

---

## Theme files

- [`parity-matrix.md`](parity-matrix.md) — expected vs observed matrix
- [`silent-exceptions.md`](silent-exceptions.md) — F1–F4, F10, encoding discard
- [`parity-residuals.md`](parity-residuals.md) — Path gates, password, `is_current`, STREAM_REWIND
- [`vocabulary.md`](vocabulary.md) — C1, CLI defaults, extras, concept count
- [`QUESTIONS.md`](QUESTIONS.md) — maintainer decisions + proposed pay list
- [`repro/`](repro/) — probe + behavioural repros
- [`tests/test_guardrails.py`](tests/test_guardrails.py) — pin today (law + accident red)
