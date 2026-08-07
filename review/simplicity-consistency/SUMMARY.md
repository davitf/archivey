# Simplicity & consistency pass — SUMMARY

**Review of** `main` @ `2792f9c` (post-#225, post-#227/#228; the three #225 changes are
archived, `seekable-gzip-and-block-writing` is the only live change).
**Status:** analysis complete for the surface the brief scoped; **no library changes**.
Artifacts here are evidence and guardrails only (`brief.md` §Hard constraints).

---

## Headline

> **The uniform interface holds. The uniformity leak is one axis down: a *capability
> flag* changes *metadata*.**

Twenty-four of the twenty-five corpus format keys were probed by execution across ~40
caller-visible operations (`repro/matrix.md`). On the reader surface the library is
strikingly consistent — `len()`, `in`, `get`, `read` of a missing name, `open()` of a
directory member, overlapping `open()`, `seek()` without the flag, close lifetime, and
the whole streaming-enforcement block behave **identically on every measured backend**.
That is a real result and most of this review's value is in pinning it (39 passing
guardrails in `tests/test_review_simplicity_consistency.py`).

The divergences that survived verification are not on that surface. They cluster in a
place no seed pointed at: **`seekable_members=True` — documented as "seek() on a member
stream works" — also decides whether `member.size` and `member.hashes` are populated**
for xz and lzip. A caller doing archivey's founding use case (open, hash, dedupe) gets
no lzip CRC-32 unless they ask for an unrelated capability. That is one root cause, one
fix, and it is the review's only headline.

Everything else is smaller: three arguments that are silently discarded rather than
refused, one spec↔code disagreement, and one entry-point asymmetry.

---

## Top findings

Severity: **S1** would embarrass at release · **S2** freeze-cost is real · **S3** tidy-up.
Status: `CONFIRMED` = runnable repro committed.

| # | Finding | Severity | Where | Status | Class | Proposed disposition |
|---|---|---|---|---|---|---|
| **P1** | `seekable_members=True` / `open_stream(seekable=True)` silently changes **member metadata**: xz `size` `None`→int, lzip `size` `None`→int **and** `hashes` `{}`→`{CRC32}`. Not source-shape dependent (Path and `BytesIO` behave alike) — the gate is the caller's flag. | **S1** | `internal/streams/codecs.py:1187`, `:1211`; `internal/backends/single_file_reader.py:294` | `CONFIRMED` | **Accident** (root cause: cheap trailer/index metadata is harvested *through* the seek-index machinery, which only exists when the flag is set) | **Pay before `0.2.0`.** Vehicle: OpenSpec change (cross-format contract) + bugfix. → Q1 |
| **P2** | The directory backend returns `None` from `members_report_if_available()` in both modes; `access-mode-and-cost` lists "Leading (directory, ISO)" as *"Both modes, as complete report"*. | **S2** | `openspec/specs/access-mode-and-cost/spec.md:96` vs `internal/backends/directory_reader.py` | `CONFIRMED` | **Spec↔code disagreement** — cannot be resolved by a reviewer (`CONTRIBUTING.md` pause-and-ask) | **Decide, then align one side.** Vehicle: spec change *or* bugfix. → Q2 |
| **P3** | `open_archive(iso_path, format=ArchiveFormat.TAR)` returns a working reader reporting `format=TAR` with **zero members and no error** — `strict_archive_eof=True` does not catch it. Same failure class #225/P8 fixed for directories. | **S2** | `core.py:200-296` (no post-open format plausibility gate) | `CONFIRMED` | **Accident with a format-law component** (an ISO's zero-filled 32 KiB system area *is* a valid empty TAR) | Decide whether "asserted format, empty result" must be loud. → Q3 |
| **P4** | `encoding=` is honoured by ZIP/TAR and **silently discarded** by ISO, 7z, directory and single-file. Exactly the argument-discard class #225/P8 turned into an error for directory `format=`. | **S2** | `core.py:279-283` → `backend.open_read(encoding=…)` | `CONFIRMED` | **Accident** (the *policy* is unstated; the discard is uniform-by-omission) | Refuse, or document per-format. → Q4 |
| **P5** | Whether a format can be read from a pipe is **not queryable**. The refusals themselves are loud and uniform (good), but `FormatAvailability` has only `format`/`support`/`missing` — a caller must try-and-catch. VISION: differences should be *data*. | **S2** | `internal/registry.py` `FormatAvailability`; `ReadBackend.SUPPORTS_STREAMING_NON_SEEKABLE` is internal | `CONFIRMED` | **Accident** (freeze-cost: `FormatAvailability`'s shape is public and freezes at the tag) | Add a capability axis before `0.2.0`, or accept. → Q5 |
| **P6** | `open_stream(directory_path)` raises `FileNotFoundError("Compressed stream not found: …")` for a path that **exists**, while `open_archive(same_path)` opens it as a directory archive. | **S3** | `core.py:332-333` | `CONFIRMED` | **Accident** (fabricated `FileNotFoundError` with the wrong story) | Message/type fix. → Q6 |
| **P7** | `cli/progress.py` and `cli/test_cmd.py` import `ExtractionProgress` from `archivey.internal.extraction_types`; `cli/extract_cmd.py` uses the public path. | **S3** | 2 files | `CONFIRMED` | **Vocabulary leftover**, pre-answered by the brief | Trivial spelling fix; **isolated** — no second instance found. |
| **P8** | No `warnings.warn` call exists anywhere in `src/`. The O-23 question ("emit a plain warning on solid random open?") is therefore still **undecided in code as well as on paper**. | — | — | Verified absent | **Open decision** | Decide or record as still open. → Q7 |
| **P9** | The 41 RAR cases of the cross-format conformance sweep run on **no CI leg and in no provisioned dev environment** — `.github/workflows/ci.yml:187` installs `unrar` only and deletes `rar` on macOS on purpose. | **S2** | `.github/workflows/ci.yml:184-199`, `scripts/setup-dev-env.sh:118` | Verified, **deliberate** | **Explicit decision** — surfaced, not re-litigated | Confirm the trade-off still holds. → Q9 |

**Freeze-cost note.** Per §Hard constraints, freeze-cost is only ever an argument for
fixing something *before* the tag, never for accepting it. P1 and P5 are the two whose
cost is genuinely post-tag: P1 because callers will start relying on "size is None
unless I pass the flag", P5 because `FormatAvailability`'s field set is public.

---

## Where the divergences cluster

Three clusters, and the shape of each is the useful part:

1. **Capability flags leaking into the data model** (P1). One flag, two meanings. The
   library got here honestly: for xz and lzip the size/CRC *live in* the seek index, so
   reusing the index machinery to read them was the short path. gzip, which reads its
   CRC from a plain bounded trailer peek, has no such coupling and is correct.
   **The fix is to make xz/lzip look like gzip, not to document the difference.**
2. **Explicit caller assertions that are discarded rather than refused** (P3, P4).
   #225/P8 established the rule for one instance (directory `format=`); the rule was
   never generalized. Two more instances exist.
3. **Entry-point asymmetries** (P5, P6). `open_archive` and `open_stream` disagree about
   what a directory is; neither exposes source-shape capability as data.

Cluster 2 is the one with a single cheap generalization available before the tag.

---

## What is actually fine

Recorded so the next review does not re-derive it. Several of these are seeds the brief
flagged as suspicious that turned out to be non-issues — which is a finding.

### Seeds that resolved to "fine"

| Seed | Verdict | Evidence |
|---|---|---|
| **A4 pipe / non-seekable matrix** — "is every refusal loud and uniform, or are there soft failures?" | **Fine.** Every trailing-index format (ZIP, ISO, 7z) refuses a pipe with one `StreamNotSeekableError` and one message shape, in both `open_archive` and `extract()`. No soft failures found. The only residual is *queryability* (P5). | `repro/matrix.md` row E4; `test_trailing_index_formats_refuse_a_pipe_loudly` |
| **A1 password / open laziness** — "are there sibling sites where work happens earlier than the docs promise?" | **No siblings found.** ISO open, ZIP ZipCrypto confirm and encrypted-header paths were probed; none does eager password work at `open_archive()`. | `repro/matrix.md` E6; corpus `encrypted` / `encrypted-header` entries opened without a password |
| **A2 Path vs seekable `BinaryIO` gates** — the 27 `isinstance(…, Path)` sites | **Fine post-#225.** Every metadata probe measured behaves identically for a `Path` and a seekable `BytesIO`. The gates that remain are genuine path-only affordances (independent FDs, `os.path.getsize`). P1 *looks* like this seed but is **not** — its gate is a caller flag, not the source shape. | flag × shape table in `parity-matrix.md` §P1 |
| **A7 duplicate-name / `is_current`** | **Fine.** `_apply_last_entry_wins_is_current` is the single driver; `get()` is last-wins on every measured backend. | `test_reader_surface_is_uniform_across_formats` |
| **A8 cost-receipt honesty** | **Fine.** Every specced example row reproduces exactly (ZIP `INDEXED`+`DIRECT`; plain TAR `REQUIRES_SCANNING`+`DIRECT`; `.tar.gz` `REQUIRES_DECOMPRESSION`+`SOLID`; solid 7z `INDEXED`+`SOLID`). `notes` is empty everywhere — never used as an occurrence log. | `repro/matrix.md` rows C1–C6 |
| **B4 error-translation consistency** | **Fine on the probed paths.** No raw `ValueError` / `RuntimeError` / `NotImplementedError` crossed the public boundary in ~1000 probe calls. The raw raises that exist in `src/` are deliberate loud invariants with comments explaining why (`zip_reader.py:727` on a missing `ZipInfo._raw_time`, `zip_reader.py:217` unreachable-cp437). | `repro/matrix.md`; `grep` sweep in `silent-exceptions.md` |
| **C5 CLI import paths** | **Isolated, as the brief predicted.** Exactly one type, two files. No second instance of the pattern; the CLI reaches into `internal/` for nothing else. | P7 |
| **D1 pass-driver / member-list leftovers** | **No regression.** No third copy or backend-local bypass introduced by #184/#225. | `base_reader.py` single `_materialize_members` / `_drive_pass_streams` |
| **D2 reader close vs stream lifetime** | **Settled and uniform.** `reader.close()` closes member streams on all 24 measured keys; a later read raises `ValueError` (stdlib shape) and a later reader op raises `ArchiveyUsageError`. | rows R9/R10 |

### Spec clauses that are honest

The O-23 sweep over **landed** capabilities (Phase 8/9 specs excluded per §B carve-out)
found the "specified but never implemented" class largely paid off by #225:

- `format-zip` "SHALL emit a `diagnostics` warning … identifying the chosen encoding" →
  implemented as `MEMBER_NAME_ENCODING_INFERRED`.
- `format-detection` conflict warning → `FORMAT_EXTENSION_CONFLICT`.
- `packaging-and-extras` "integrity diagnostic instead of failing the read" →
  `DIGEST_UNVERIFIABLE`.
- `seekable-decompressor-streams` slow-rewind warning → `STREAM_REWIND_REDECOMPRESSES`.
- `archive-reading` "no diagnostic, no warning" for solid random `open()` → correctly
  **absent** (no `warnings.warn` in `src/`).

One clause did not survive: `format-single-file-compressors`'s **XZ** size row
(*"Header size when encoder wrote it; otherwise `None`"*) carries **no** seekability
condition, but the code only surfaces it under `seekable_members=True`. That is the
spec-honesty half of P1 and is filed there rather than counted twice.

### The negative result the brief asked to preserve

The CLI does **not** reach into `internal/` for anything it lacks a public route to.
The addendum's "CLI reaching into `internal/` is usually an API gap" heuristic finds
nothing here, exactly as the brief recorded. No second pattern emerged.

---

## Deliverables map

| File | What it is |
|---|---|
| `SUMMARY.md` | this file |
| `expected.md` | the matrix's **expected** column, written from `VISION.md` + `openspec/specs/` alone **before** the probe ran (brief's seed counterweight), with its contamination disclosure |
| `parity-matrix.md` | expected vs observed, the diff, and the O-21 trace for each divergence |
| `silent-exceptions.md` | the argument-discard / spec-honesty / error-translation sweeps |
| `vocabulary.md` | surface-vocabulary leftovers that freeze at the tag |
| `QUESTIONS.md` | nine maintainer decisions, each with severity, fix vehicle, and a recommendation |
| `repro/probe_matrix.py` | the generator — runs the whole matrix by execution |
| `repro/matrix.md`, `repro/matrix.json` | its output at `2792f9c` |
| `tests/test_review_simplicity_consistency.py` | 39 guardrails + 10 strict-xfail red halves |

## Baseline (this environment, `[all]` config)

| Check | Result |
|---|---|
| `pytest` | **2132 passed, 65 skipped**, 3 deselected — 87% coverage |
| `ruff check` / `ruff format --check` | clean / 187 files formatted |
| `pyrefly check` | 0 errors |
| `ty check` | clean |
| `openspec validate --all` | 25 passed, 0 failed |
| `format_availability()` | **every** known format `FULL` — no unmeasured *reader* |
| Corpus coverage | 24 of 25 format keys measured; **`rar` unmeasured** (writer absent by design — P9) |

Three-config runs were not needed: no finding here depends on an optional library's
presence or version — P1–P6 all reproduce with the stdlib codecs plus `[recommended]`.

## Not covered

Stated so the gaps are visible rather than implied:

- **RAR** — the whole column (P9). Every RAR row in `repro/matrix.md` is `unmeasured`.
- **Multi-volume** behaviour (7z `.001`, RAR volume sets) — the corpus builds none in
  this environment and the brief did not scope it.
- **Free-threaded / concurrent** rows — `reader-concurrency` was treated as settled
  ground (`brief.md` §E) and only its single-live-stream gate was probed.
- **Damaged-input read salvage** — `VISION.md` records it as a known gap and
  `IDEAS.md` owns it; the review checked only the *listing* honesty contract (X9),
  which holds.
