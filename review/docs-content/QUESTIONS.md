# QUESTIONS — Topic 8 pass 1 (capability verification)

Maintainer decisions with fix vehicles. One question per item. Cold-readable.
Produced after merging nine capability-worker verdicts into `claims.md` (2026-08-18).

Known upcoming decisions (do **not** block verification; surface if verdicts bear on them):

- **P11** (`dev-docs/open-issues.md`): RAR stream temp spill needs a signal —
  `CostReceipt.notes`, a diagnostic, or both. Blocks writing E-71's prose, not verifying.
- **scope.md Q3 / §D**: whether `api.md` enumerates the 21 undocumented exception types.
  Timing only; blocks writing `errors-and-diagnostics.md`.

---

## O-26 classification of every `wrong` verdict

Eleven rows. Prefer **spec** over code when both exist.

### Prose is wrong → pass 1 page PRs

| ID | What's wrong | Evidence | Proposed vehicle |
|---|---|---|---|
| **A-6** | Absolute “never silently buffers” reading; ADR 0010 is pipe/non-seekable-scoped; RAR-from-seekable-stream contradicts absolute reading (E-71) | Coordinator repro; `opening-and-listing.md:25-28`, `philosophy.md:42`, `migrating.md:170-172`, `access-and-cost.md:141-143` | Page PR: reword to the scoped claim; link E-71 / P11 |
| **A-16** | `access-and-cost.md:145-146` names only ZIP+ISO as always needing seek | Coordinator pipe matrix; `required_source=SEEKABLE` for ZIP/7z/RAR/ISO | Page PR on `access-and-cost.md`: list all four (or point at `required_source`) |
| **A-18** | Sample passes `FormatInfo` into `format_availability` | Runtime `ArchiveyUsageError`; needs `.format` | Page PR: `opening-and-listing.md:74-81` |
| **A-33** | Inventory claimed same-situation conflict; **false** | `PackageNotInstalledError` = member/AES backend; `UnsupportedFormatError` = format `NONE` at open | No SPLIT. Optional clarify-on-pages PR so readers don't merge the two; inventory claim stays `wrong` |
| **D-48** | `VerificationMode.STRICT` documented as shipped | Not in `src/` or current specs; open `verification-integrity-mode` | Page PR: remove or mark unshipped (`errors-and-diagnostics.md:178`) |
| **E-1** | Formats matrix: Directory Listing = “indexed” | Spec `ListingCost.REQUIRES_SCANNING`; spot-check | Page PR: `formats.md` matrix cell → scanning / not indexed |
| **E-71** | Silence: RAR seekable-stream whole-archive temp spill | Coordinator repro; `_ensure_archive_path` | **Docs half:** new prose (page TBD — formats §RAR or access-and-cost). **Library half:** P11 (below) |
| **F-16** | Solid “do this” `stream_members` sample calls `consume(stream)` on dirs (`stream is None`) | `AttributeError` on `basic_solid__.rar` | Page PR: `access-and-cost.md` sample — None-guard |
| **F-26** | AUTO “only when seek + size ≥ 1 MiB” overstates | Spec: unknown size still eligible; DEFLATE also needs verifiable size | Page PR: align with `seekable-decompressor-streams` / `config.py` |
| **G-9** | `[free-threaded]` “exactly … `backports.zstd`” omits `<3.14` gate | Spec/`pyproject`: zstd backport only `<3.14`; 3.14+ uses stdlib + `cryptography` | Page PR: `acknowledgements.md` / install free-threaded wording |

### Code is wrong → separate red–green PR (#225/#250 shape)

| ID | Defect | Triggering input | Config | Proposed vehicle |
|---|---|---|---|---|
| **A-34** | `detect_format` / auto-open do not implement SFX scan; `format-detection` **requires** SFX behind executable stub; guide matches the **spec**. **Worst path is silent:** low-entropy `MZ` stub can misdetect as `BROTLI` and `open_archive` succeeds with a fabricated `*.uncompressed` member — no exception, no diagnostic | See trigger matrix below (not only `FormatDetectionError`) | `[all]` | Library PR implementing SFX window in detection (or OpenSpec retract — Q1). **Regression test must cover the silent-success path**, not only the raising case. Priority: see review MD1 |

**A-34 trigger matrix** (reproduced `[all]`, 4 KiB stub + real payload):

| Stub filler | `detect_format` | `open_archive` (auto) | forced `format=` |
|---|---|---|---|
| `MZ` + `0x90`×4094, RAR | **`BROTLI` / PROBABLE** (`content_probe`, `payload_offset=0`) | **OK** — bogus member `sfx.exe.uncompressed` | `RAR` → OK, real members |
| `MZ` + varied bytes, RAR | `FormatDetectionError` | `FormatDetectionError` | `RAR` → OK |
| `MZ` + varied bytes, 7z | `FormatDetectionError` | `FormatDetectionError` | `SEVEN_Z` → **`CorruptionError`** (no 7z SFX scan) |
| `MZ` + `0x90`×4094, 7z | **`BROTLI` / PROBABLE** | **OK** — bogus member | `SEVEN_Z` → **`CorruptionError`** |

Forced-format escape is **RAR-only** (`rar_parser._find_sfx_header`, `SFX_MAX=2MiB`). 7z has no equivalent.

### Spec is wrong → OpenSpec change

None of the eleven `wrong` rows is *only* a spec defect. Two **verified** guide claims expose spec drift and need a decision (Q2, Q3 below).

---

## Q1 — A-34: implement SFX detection, or retract the spec?

**Context.** `openspec/specs/format-detection/spec.md` §“Self-extracting (SFX) archives are detected behind an executable stub” requires detecting RAR/7z behind an executable header (`payload_offset > 0`). Guide `formats.md:225-226` states that. Shipped `detect_format` has no SFX scan (module comment: deferred).

Forced `format=RAR` still opens via the RAR parser's SFX window. Forced `format=SEVEN_Z` on the same stub raises `CorruptionError` (bad magic) — **7z has no parser-side SFX scan**. Separately, a low-entropy stub can misdetect as `BROTLI` and open successfully with a fabricated member (silent wrong answer).

**Options**

1. **Implement** SFX detection in `detect_format` / auto-open (prefer: matches current spec + guide; also closes the silent `BROTLI` path and gives 7z a working SFX path).
2. **Retract** the format-detection SFX requirement and rewrite the guide. Escape hatch is **RAR-only today**; under a retract, **7z SFX would have no working path** until a separate 7z parser SFX scan lands. Do not advertise “forced `format=`” as a general fallback.
3. **Document as known gap** pointing at an issue, leave spec as aspirational (weak — contradicts “specs are authoritative”).

**Recommendation:** (1). Preferring the spec is O-26; Option 2 is costlier than it first reads because of the 7z hole and the silent misdetect.

**Blocks:** writing the formats Detection / SFX sentence in pass 1.

---

## Q2 — C-25: sticky bit under `STANDARD` — code or spec?

**Context.** Guide: setuid/setgid/**sticky** stripped except under `TRUSTED`. Spot-check: sticky stripped under `STRICT`/`STANDARD`, kept under `TRUSTED`. `safe-extraction` metadata matrix: `STANDARD` strips **setuid/setgid only** (sticky preserved).

**Options**

1. **Code is wrong** — preserve sticky under `STANDARD` to match the matrix; update tests.
2. **Spec is wrong** — change the matrix to “strip all three under STANDARD”; guide already matches code.
3. **Guide is wrong** — rewrite to match the matrix and change code to preserve sticky (same as 1 from the reader's view).

**Recommendation:** Decide from the original ADR / threat-model intent for `STANDARD` vs `STRICT`. If `STANDARD` is meant to be “tar-like metadata fidelity without ownership,” sticky often stays with execute bits — that argues (1). If `STANDARD` is “strip privilege bits,” sticky is privilege-adjacent — argues (2).

**Blocks:** extracting.md metadata sentence; any → DS promotion of the policy matrix.

---

## Q3 — B-26: foreign `ArchiveMember` → `ValueError` or `ArchiveyUsageError`?

**Context.** Guide + runtime + `error-handling` → `ArchiveyUsageError`. `archive-reading` Reading member data matrix still says `ValueError`.

**Options**

1. **Align `archive-reading` to `ArchiveyUsageError`** (matches code + error-handling + guide).
2. **Change code to raise `ValueError`** (breaks callers catching `ArchiveyUsageError`; unlikely).

**Recommendation:** (1) — OpenSpec delta on `archive-reading`.

**Blocks:** nothing for prose (guide already correct); blocks treating archive-reading as the Settles-it authority for this row.

---

## Q4 — E-71 / P11: how should the RAR stream spill be signalled?

**Context.** Verified: seekable stream + non-direct-readable RAR member → whole archive copied to temp `.rar`; absent from `CostReceipt.notes` and diagnostics; cleaned on close. Docs silence is E-71 (`wrong`). Honest-cost half is P11.

**Options** (from open-issues; do not invent new ones without reading P11)

1. `CostReceipt.notes` entry only.
2. Diagnostic only.
3. Both.

**Recommendation:** whichever P11 already leans toward; docs prose for E-71 should wait until the signal exists so the page names a real field.

**Blocks:** writing E-71 prose (not verifying).

---

## Q5 — scope.md Q3 / §D: enumerate the 21 undocumented exception types in `api.md`?

**Context.** D-8 style completeness: exception table / `api.md` name 12 of ~26 types; 21 exceptions lack mkdocstrings entries. Timing decision only.

**Options** — as already framed in `scope.md` / brief §D (enumerate all / documented subset + corrected sentence / generate).

**Recommendation:** do not reopen here; answer on the existing Q3 thread.

**Blocks:** writing `errors-and-diagnostics.md` depth / → DS routing for the exception tree.

---

## Verified defect / silence claims (not `wrong`, but need prose or vehicles)

These rows are **verified** as “the defect/silence exists.” They are not in the wrong table because the *claim about the defect* is true.

| ID | Kind | Note |
|---|---|---|
| **C-42** (S-2) | Prose defect | Policy table missing `STANDARD` row |
| **C-66** | Silence | `extract_all(config=)` cannot raise listing ceiling set at open |
| **D-8 / D-51–53** | Silence / incompleteness | Exception table / translation narrative gaps |
| **F-3** (O-4) | Stale link | `archivey-2` nightly |
| **F-7** | Completeness | `CostReceipt` table omits `notes` |
| **H-16 / H-17** | Silence | Terminal escaping / password-argv documentation gaps (help already warns) |
| **I-22 / I-24** | Silence / dangling | `api.md` `__all__` gaps; bare `IDEAS.md` refs |

---

## Review round-1 dispositions (PR #252)

| ID | Disposition | Note |
|---|---|---|
| F1 | **Fixed** | `G-25a` → `verified`; counts 401→402 / verified 381→382 |
| F2 | **Fixed** | A-34 trigger matrix + Q1 Option 2 corrected (RAR-only forced-format; silent `BROTLI` path) |
| F3 | **Fixed** | Twelve `cfg` rows restored to `verified · cfg `[all]``; per-cluster evidence pointers added (MD3 option B) |
| F4 | **Fixed** | Sweep restated via `list_known_formats()` (26×FULL); SESSION + claims re-measure note |
| F5 | **Disproven** as contradiction; **deferred** clarity to page PR | `gotchas.md:45-48` is true for its own trigger; suggest naming the trigger / linking the path residual when that page is rewritten. Home: pass-1 `gotchas.md` page PR worklist |
| MD4 (validator) | **Deferred** | Small `claims.md` completeness check (non-empty V; stated counts match). Home: `review/backlog.md` under Topic 8 follow-ups / Definition-of-done row 8 |

Maintainer product calls still open: **MD1** (A-34 priority — silent path vs normal library PR) below; Q2–Q5 unchanged.

---

## Where the `wrong` verdicts concentrate

| Surface | Wrong IDs | Pattern |
|---|---|---|
| **`access-and-cost.md`** | A-6, A-16, F-16, F-26 (+ E-71 docs home TBD) | Cost/honesty page carries the densest load: seek list understatement, absolute buffering, broken solid sample, overstated AUTO rule |
| **`opening-and-listing.md`** | A-6, A-18 | Sample type error; buffering absolute wording |
| **`formats.md`** | E-1, A-34 (Detection/SFX), A-33 (false conflict inventory) | Matrix cell + SFX ahead of detection code |
| **`errors-and-diagnostics.md`** | D-48 | Unshipped `VerificationMode.STRICT` |
| **`acknowledgements` / install free-threaded** | G-9 | Version-gated extra membership overstated |

`reading-members`, `extracting` (aside from C-42 table), `cli`, and `philosophy` positioning rows are comparatively clean on explicit `wrong`s; extraction's debt is mostly `[TM]` deferrals and the STANDARD table/sticky questions.
