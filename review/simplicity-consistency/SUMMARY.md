# Simplicity & consistency — SUMMARY

**Headline.** After `#225`, the uniform-interface surface is mostly honest: directory
`format=` is loud, seekable-stream size/CRC probes work for gzip hashes, solid
password confirm is deferred, and codec install hints point at `[recommended]`.
What remains is a smaller set of **silent knobs**, **error-boundary gaps**, one
**usage-side diagnostic**, and **docs/spec fiction** that still teach the wrong
story. No third pass-driver copy returned after `#184`.

**Baseline (this run):** `unrar` + `7z` on `PATH`; branch off `2792f9c` (post-#225
/#227/#228). Evidence scripts: `repro/repros.py`, `repro/probe_parity_matrix.py`.
Config: `[all]`.

**Verdict for pay-before-tag:** several cheap bugfix / docs / spec items;
vocabulary C1 and CLI defaults are product decisions (already decided or freeze
questions). Do **not** treat remaining Path open/materialize sites as capability
gates.

---

## Top findings (severity × confidence)

| ID | Sev | Conf | Status | Where | Disposition / vehicle |
|---|---|---|---|---|---|
| F1 | High | CONFIRMED | Accident | `volumes.py` → `open_archive([])` / non-seekable volume streams | **Bugfix** — translate to `ArchiveyUsageError` / `StreamNotSeekableError` |
| F2 | High | CONFIRMED | Accident | `encoding=` on 7z/RAR/dir/ISO/single-file silently discarded | **Bugfix or product** — reject like passwords, or document + format-scoped SUPPORTS_ENCODING |
| F3 | Med | CONFIRMED | Accident | ZIP `ValueError("…already closed")` → `CorruptionError` via public `open` | **Bug fix** — carve closed-archive out of ZIP ValueError→Corruption map; prefer `ArchiveyUsageError` |
| F4 | Med | CONFIRMED | Spec fiction | `testing-contract` RTL "warns **or rejects**"; code only `logger.warning` | **Spec change** — drop "or rejects"; optionally promote to diagnostic |
| F5 | Med | CONFIRMED | Awkward | `STREAM_REWIND_REDECOMPRESSES` describes **caller seek**, not archive | **Decide** (O-23 open) — keep / demote to `warnings.warn` / rename taxonomy |
| F6 | Med | CONFIRMED | Residual Path gate | single-file `compressed_size` only when `isinstance(Path)` | **Bug fix** — fill from seekable `SEEK_END` like other probes |
| F7 | Low | CONFIRMED | Docs stale | `must-explain` #25 still says directory `format=` is overruled | **Docs-only** — cite `#225` reject |
| F8 | Low | CONFIRMED | Vocabulary | `MemberStreams` / `seekable_members` vs `open_stream(seekable=)` | **Product** — freeze question C1 |
| F9 | Low | CONFIRMED | Product | CLI overwrite/`OnError`/dest ≠ library | **Accept** — `cli-product` Q1 + must-explain #23 |
| F10 | Low | PLAUSIBLE | Leak | `rar_unrar` `RuntimeError("unrar produced no stdout pipe")` untranslated | **Bug fix** — map in RAR translator (defensive; hard to hit) |
| F11 | Info | CONFIRMED | Fine | Extras hints no longer say `[7z]` for Deflate64/PPMd | **Accept** — settled |
| F12 | Info | CONFIRMED | Fine | `_apply_last_entry_wins_is_current` + RAR `path;n` | **Accept** — one story |
| F13 | Info | CONFIRMED | Fine | Pass-driver: single `_drive_pass_streams`; no third copy | **Accept** |
| F14 | Low | CONFIRMED | Concept tax | gotchas ~7 / opening ~11 / reading ~1 format-conditionals; 29 must-explain | **Rank** — library simplicity signal, not a bug |
| F15 | Low | CONFIRMED | Format law | Header-encrypted 7z/RAR password at **open**; data encrypt stays lazy | **Accept + docs** — caveat the reading-members laziness bullet |
| F16 | Low | PLAUSIBLE | Config no-op | `zip_unflagged_fallback_encoding` / TAR-only `strict_archive_eof` on other formats | **Accept or document** — format-scoped config |

---

## What is actually fine

- **Directory `format=`** — rejected loudly (`core.py:202–211`); `#225` paid.
- **Password on non-encrypting formats** — central `SUPPORTS_PASSWORD` reject.
- **Seekable BinaryIO gzip CRC** — filled on `BytesIO` after `#225`; residual is
  **compressed_size** only (F6).
- **lzip digest path** — `_probe_lzip_index` uses `_with_seekable_source` (Path *or*
  seekable stream); not Path-gated.
- **Solid 7z/RAR folder password confirm** — deferred to first member open (`#225`).
- **Install hints** — codecs/backends say `pip install archivey[recommended]` /
  `[seekable]`; no live `[7z]` leftover in `src/`.
- **Duplicate `is_current`** — ZIP/TAR via `_apply_last_entry_wins_is_current`
  (`base_reader.py:87–106`); RAR history `path;n` + `is_current=False` preserved
  for unique names (`rar_reader.py:604`, helper docstring).
- **Pass-driver** — one shared `_drive_pass_streams`; TAR/RAR/7z call it; indexed
  backends use the default `_iter_with_data`. No post-`#184` third copy.
- **CLI reaching into `internal/`** — only `ExtractionProgress` import-cycle
  spelling; type is public (`brief` §C negative result).
- **Pipe refusals** — loud `StreamNotSeekableError` (format law for ZIP/ISO index).
- **Solid random-open cost** — silent by design (`archive-reading` spec: no
  diagnostic; discover via `access_cost`). Spec fiction for solid *warning* already
  dropped (`#225`).

---

## Concept count (caller-facing simplicity)

| Page | Lines | Likely format-conditionals | Notes |
|---|---:|---:|---|
| `docs/gotchas.md` | 88 | ~7 | Digest of traps; links out |
| `docs/opening-and-listing.md` | 179 | ~11 | Sources, volumes, passwords, duplicates |
| `docs/reading-members.md` | 177 | ~1 | Mostly uniform; solid cost caveat |
| `must-explain.md` | 29 items | — | Behaviours not inferable from signatures |

Must-explain items that are **consistency / dual-default** (not pure format law):
**#4** pipe vs `extract`, **#9** duplicates/`is_current`, **#10** / **#11** solid vs
concurrent, **#13** passwords, **#16** accelerator AUTO, **#21** `open_stream` vs
`open_archive`, **#23** CLI vs library, **#25** multi-volume / directory (stale half).

**Seed A note (TAR corrupt final header):** a naive trailing `X*512` after a valid
TAR no longer raises on RA nor emits EOF diagnostics in streaming (`repro` R2).
The formats.md / open-issues P3 residual may need a stronger fixture; not treated
as a live accident in this pass.

---

## Theme files

- [`silent-exceptions.md`](silent-exceptions.md) — F1–F4, F10, encoding discard
- [`parity-residuals.md`](parity-residuals.md) — Path gates, password laziness,
  `is_current`, STREAM_REWIND
- [`vocabulary.md`](vocabulary.md) — C1, CLI defaults, extras, concept count
- [`QUESTIONS.md`](QUESTIONS.md) — maintainer decisions
- [`repro/`](repro/) — matrix probe + behavioural repros
