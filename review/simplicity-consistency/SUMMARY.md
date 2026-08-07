# Simplicity & consistency pass — SUMMARY (merged)

**Review of** `main` @ `2792f9c` (post-#225, post-#227/#228; the three #225 changes are
archived, `seekable-gzip-and-block-writing` is the only live change).
**Status:** analysis complete, **all 16 questions decided** (2026-08-07 — see
[`QUESTIONS.md`](QUESTIONS.md) for the rulings and the re-ranked pay list); **no library
changes in this PR**. Artifacts here are evidence and guardrails only
(`brief.md` §Hard constraints) — each decision lands as its own change.

Two rulings went **against** this review's recommendation, deliberately: **Q13**
(reopen `STREAM_REWIND_REDECOMPRESSES`'s placement) and **Q16** (treat the `seekable`
vocabulary split as live rather than settled by spec).

**Q13 has since been worked and vindicated the override.** Pursuing it produced a
resolution neither pass reached — the O-23 *rule* is under-evidenced, not the code — plus
a new `CONFIRMED` finding, **F19**, that the probe could not have found: the rewind
diagnostic is silent for a degenerate seek index, which makes its one real job (the
`RAISE` tripwire) unreliable. Drafts and evidence:
[`q13-rewind-diagnostic.md`](q13-rewind-diagnostic.md).

**Round 2 (2026-08-07).** The residue of the sixteen was written up as
[`open-questions-for-discussion.md`](open-questions-for-discussion.md) (O1–O8) and sent
for outside comment; two independent reviews came back and **eight of the ten items are
now resolved**, including the O-23 `warnings.warn` sub-question above (**O2a: no**). Two
of this review's own round-1 leanings were **reversed on argument** — O1's threshold
(relative → absolute, because relative goes quietest on the most expensive seek) and O8's
empty-TAR question (open → definitely-not-raise, because a legitimately empty tar is
10240 zero bytes, byte-identical to garbage, which also killed the middle option this
review proposed). One genuinely new question surfaced and is the only unanswered item in
the review: **O2c**, what decoder reuse means when members are opened concurrently.
Resolutions are indexed in [`QUESTIONS.md`](QUESTIONS.md) §Round 2 and argued in full in
the discussion document.

> **This is a merge of two independent passes.** The brief was executed twice without
> either agent seeing the other: **PR #230** and **PR #231**. Both built an expected
> column from `VISION.md` + specs, then a probe-generated observed column. They agreed
> on the shape of the surface and found **largely disjoint** defects — which is the
> useful result. Every finding below was re-verified in this tree before merging;
> nothing was taken on the other pass's word. The provenance column records who found
> what, and §Where the two passes disagreed records the three places they reached
> different verdicts on the *same* evidence.

---

## Headline

> **The uniform interface holds. What is left is a set of small, unrelated accidents —
> and one that is not small: a *capability flag* changes *metadata*.**

Twenty-four of the twenty-five corpus format keys were probed by execution across ~45
caller-visible operations (`repro/matrix.md`). On the reader surface the library is
strikingly consistent — `len()`, `in`, `get`, `read` of a missing name, `open()` of a
directory member, overlapping `open()`, `seek()` without the flag, close lifetime, and
the whole streaming-enforcement block behave **identically on every measured backend**.
Both passes reached that conclusion independently, which is the strongest evidence in
this review. Most of its value is in pinning it (54 passing guardrails in
`tests/test_review_simplicity_consistency.py`).

The one finding that is not a tidy-up is **F1**: `seekable_members=True` — documented as
"`seek()` on a member stream works" — also decides whether `member.size` and
`member.hashes` exist for xz and lzip. A caller doing archivey's founding use case
(open, hash, dedupe) gets no lzip CRC-32 unless they ask for an unrelated capability.
One root cause, one fix.

Below that, the accidents cluster into three recognisable shapes: **explicit caller
input discarded rather than refused** (F2, F7), **error typing that stops at the entry
point** (F3, F4, F15), and **a field, capability or signal gated on the wrong thing**
(F1, F5, F8, F19).

**F19 was found by working a decision, not by the probe.** Q13 was reopened against both
passes' recommendation; pursuing it surfaced that the rewind diagnostic's predicate is
codec identity, so a single-block `.xz` re-decodes its whole stream on a backward seek
and says nothing — including to a caller who armed `DiagnosticPolicy` `RAISE` precisely
to catch that. See [`q13-rewind-diagnostic.md`](q13-rewind-diagnostic.md).

---

## Top findings

Severity: **S1** would embarrass at release · **S2** freeze-cost or contract honesty ·
**S3** tidy-up. Status: `CONFIRMED` = runnable repro committed and re-verified here.
Provenance: **230** = found by PR #230 · **231** = found by PR #231 · **both**.

| # | Finding | Sev | Status | From | Class | Vehicle |
|---|---|---|---|---|---|---|
| **F1** | `seekable_members=True` / `open_stream(seekable=True)` silently changes **member metadata**: xz `size` `None`→int; lzip `size` `None`→int **and** `hashes` `{}`→`{CRC32}`. Identical for `Path` and `BytesIO`, so the gate is the caller's flag, not the source shape. | **S1** | `CONFIRMED` | 230 *(231 saw the lzip half and classified it fine — see §Disagreements)* | **Accident**: cheap trailer/index metadata is harvested *through* the seek-index machinery, which only exists when the flag is set | OpenSpec change + bugfix → **Q1** |
| **F2** | `encoding=` is honoured by ZIP/TAR and **silently discarded** by 7z, RAR, ISO, directory and single-file. `password=` on a non-encrypting format is refused; `encoding=` has no analogous gate. | **S2** | `CONFIRMED` | **both** | **Accident** — #225/P8's rule was applied to one argument only | OpenSpec change + bugfix → **Q2** |
| **F3** | `open_archive([])` → raw `ValueError`; `open_archive([pipe, pipe])` → raw `ValueError`. `resolve_source` runs before any backend translator, so nothing on that path can type them. The **single**-source version of the same refusal is a typed `StreamNotSeekableError`. | **S2** | `CONFIRMED` | 231 | **Accident** — error-contract hole at the entry point | bugfix → **Q3** |
| **F4** | Closing the underlying `ZipFile` while the reader is live reports `CorruptionError` — the ZIP translator maps **every** `ValueError` to corruption, and `"already closed"` falls in. Sends a caller hunting a bad file that is fine. | **S2** | `CONFIRMED` | 231 | **Accident** — blanket translation arm | bugfix → **Q4** |
| **F5** | `member.compressed_size` is filled from a `Path` and `None` from a seekable `BytesIO` — for **every** single-file codec, not just gzip. The trailer/CRC probes beside it already handle both shapes. | **S2** | `CONFIRMED` | 231 | **Accident** — the residual the #225 Path/seekable sweep did not reach | bugfix → **Q5** |
| **F6** | The directory backend returns `None` from `members_report_if_available()` in both modes; `access-mode-and-cost:96` lists "Leading (directory, ISO)" as *"Both modes, as complete report"*. | **S2** | `CONFIRMED` | 230 | **Spec↔code disagreement** — not a reviewer's call (`CONTRIBUTING.md` pause-and-ask) | spec change *or* bugfix → **Q6** |
| **F7** | `open_archive(iso, format=ArchiveFormat.TAR)` returns a working reader reporting `format=TAR` with **zero members and no error**; `strict_archive_eof=True` does not catch it. | **S2** | `CONFIRMED` | 230 | **Accident with a format-law component** (an ISO's zero-filled 32 KiB system area *is* a valid empty TAR) | OpenSpec change + bugfix → **Q7** |
| **F8** | Whether a format can be read from a pipe is **not queryable**. Refusals are loud and uniform (good), but `FormatAvailability` carries only `format`/`support`/`missing`; the fact lives on an `internal/` class attribute. | **S2** | `CONFIRMED` | 230 | **Accident**, real freeze-cost — `FormatAvailability` is public | OpenSpec change + impl → **Q8** |
| **F9** | Header-encrypted 7z and RAR require the password at **`open_archive()`** (format law — the listing is ciphertext), but `docs/reading-members.md` states the laziness rule without that bound: *"no password is requested for it"*. | **S2** | `CONFIRMED` | 231 *(230 stated the opposite — see §Corrections)* | **Format law + docs gap** | docs-only → **Q9** |
| **F10** | The RTL/bidi name warning is a bare `logger.warning` with **no `DiagnosticCode`** — the only advisory in the library with no queryable counterpart. `testing-contract`'s clause ("rejected **or** exactly one warning") is permissive enough that a reader cannot tell which ships. | **S2** | `CONFIRMED` | 231 *(reframed — see §Disagreements)* | **VISION warnings-as-data gap** + a vague spec clause | spec change (+ optional diagnostic) → **Q10** |
| **F11** | `open_stream(directory)` raises `FileNotFoundError("Compressed stream not found: …")` for a path that **exists**, while `open_archive(same_path)` opens it. | **S3** | `CONFIRMED` | 230 | **Accident** — one predicate collapses two situations and asserts the false one | bugfix → **Q12** |
| **F12** | `STREAM_REWIND_REDECOMPRESSES` describes the caller's seek, not the archive — the O-23 rule's awkward residual. Both passes said flag-don't-churn; **Q13 was reopened and worked**, and the resolution is that the rule is under-evidenced, not the code: the *extraction* codes do not fit the O-23 wording either. Drafts in [`q13-rewind-diagnostic.md`](q13-rewind-diagnostic.md). | **S3** | `CONFIRMED` | **both** | **Rule defect**, not a code defect | docs + observation + spec note → **Q13** |
| **F19** | The rewind predicate is **codec identity**, so a *degenerate* index is silent: a single-block `.xz` (what `lzma.compress` and un-threaded `xz` produce) has one seek point at the origin, re-decodes the whole stream on a backward seek, and emits nothing — so `DiagnosticPolicy` `RAISE` cannot fire either. The honest predicate is the seek's re-decode distance, and `DecompressorStream._seek_point_for()` already computes it. | **S2** | `CONFIRMED` | **Q13 follow-on** | **Accident** — the tripwire is unreliable where it would be depended on | OpenSpec change on `seekable-decompressor-streams` + bugfix → **Q13/F19** |
| **F20** | A file of 32 KiB zeros named `z.tar` **opens as an empty TAR** via the extension fallback — no error, no diagnostic, unchanged by `strict_archive_eof`. Content detection correctly refuses the same bytes. `strict_archive_eof` only asserts the two null trailer blocks are present; measured, it ignores 4 KiB of appended junk. | **S2** | `CONFIRMED` | **Q13/O8 follow-on** | **Accident** — the realistic layer of F7, which the F7 fix does not reach (no explicit `format=` to disagree with) | decision first (should 0 members raise?), then OpenSpec + bugfix |
| **F13** | `must-explain.md:331–335` still says a directory path forces `DIRECTORY` "even if `format=` says otherwise" — #225 made that an `ArchiveyUsageError`. | **S3** | `CONFIRMED` | 231 | **Stale docs** | docs-only → **Q14** |
| **F14** | `cli/progress.py` and `cli/test_cmd.py` import `ExtractionProgress` from `internal/`; `cli/extract_cmd.py` uses the public path. Isolated — one type, no second instance. | **S3** | `CONFIRMED` | **both** | **Vocabulary leftover**, pre-answered by the brief | 2-line fix → **Q14** |
| **F15** | `rar_unrar.py:157` raises a raw `RuntimeError("unrar produced no stdout pipe")` from call sites outside `_translated_errors`. Not reachable with a real `Popen(stdout=PIPE)`. | **S3** | `PLAUSIBLE` | 231 *(230 recorded it as "fine, noted")* | **Defensive gap** | bugfix → **Q15** |
| **F16** | The 41 RAR cases of the **corpus conformance sweep** run on no CI leg and in no provisioned dev environment — CI installs `unrar` only and deletes `rar` on macOS on purpose. RAR *reading* is still exercised via committed fixtures under `tests/fixtures/rar/`. | **S2** | Verified, **deliberate** | 230 *(corrected using 231's fixture route)* | **Explicit decision** — surfaced, not re-litigated | decision → **Q11** |
| **F17** | Concept count: `docs/gotchas.md` ~7 format-conditionals, `opening-and-listing.md` ~11, `reading-members.md` ~1; `must-explain.md` carries **29** behaviours not inferable from signatures, of which #4, #9–11, #13, #16, #21, #23, #25 are consistency-flavoured. | — | measured | **both** | **Signal, not a defect** | none — the review's before/after metric |
| **F18** | CLI defaults diverge from library defaults (overwrite `rename` vs `ERROR`; `OnError.CONTINUE` vs `STOP`; smart anti-tarbomb dest). | — | `CONFIRMED` | **both** | **Explicit decision** — `cli-product` Q1 | accept, keep `docs/cli.md` loud |

**Freeze-cost note.** Per §Hard constraints, freeze-cost only ever argues for fixing
something *before* the tag, never for accepting it. F1 and F8 are the two whose cost is
genuinely post-tag: F1 because callers will start relying on "size is `None` unless I
pass the flag", F8 because `FormatAvailability`'s field set is public.

---

## Corrections this merge forced

Recorded plainly, because a merged review that hides where one pass was wrong is worth
less than either pass alone.

1. **PR #230 said the `isinstance(…, Path)` sweep (seed A2) came back clean. It did
   not.** `member.compressed_size` is still Path-gated across every single-file codec
   (**F5**). #230's probe only ever opened a `Path` for that row, so a Path-only fill
   and a correct fill were the same cell. The merged probe now re-asks the metadata rows
   over a `BytesIO` (`H6`/`H7`), which is what makes the gap visible.
2. **PR #230 said no password work happens at `open_archive()` for any probed format,
   "including 7z header-encrypted". That is wrong** (**F9**). Its probe passed the
   corpus entry's password, so it never saw the failure. Header-encrypted 7z **and** RAR
   both raise `EncryptionError` at open — correctly, since the listing is ciphertext.
   The accurate statement is the bounded one: #225's laziness fix covers *data*
   encryption and has no siblings; *header* encryption is format law.
3. **PR #230 called the RAR column "unmeasured".** Half right: the RAR *corpus* cases
   are unmeasurable without the `rar` writer, but committed fixtures under
   `tests/fixtures/rar/` do let RAR reading be probed, which is how #231 filled its RAR
   column. F16 is narrowed accordingly.
4. **PR #231 listed the lzip digest gate under "what is actually fine"** because it is
   seekable-gated rather than Path-gated. See §Disagreements — the merged verdict is
   that "not a Path gate" does not make it fine.
5. **PR #231's guardrails lived at `review/simplicity-consistency/tests/`**, which
   `testpaths = ["tests"]` never collects. They have been moved into
   `tests/test_review_simplicity_consistency.py` so CI actually runs them.

---

## Where the two passes disagreed

Three places, on the same evidence. Each is a maintainer call, not a reviewer one.

### D1 — the lzip/xz metadata gate: accident, or fine? *(→ F1 / Q1)*

- **#231:** *"lzip digest path — seekable-gated, not Path-gated — **fine**."* The seed
  being hunted was Path-gating; this is not that, so it passes.
- **#230:** the gate is a **caller capability flag**, which is worse than a source-shape
  gate, not better — the same file with the same seekable source yields different
  metadata depending on an argument about `seek()`.

**Merged verdict: accident**, on two pieces of evidence #231 did not have.
(a) It is not only lzip: **xz `member.size`** has the same gate, and the XZ row of
`format-single-file-compressors` states its size rule with **no** seekability condition
— so a landed spec is already wrong. (b) **gzip** reads its trailer CRC-32 through a
bounded peek with no flag, so within the same file three codecs are ungated and two are
gated, purely because the gated two reuse the decompressor. Q1 puts both readings to the
maintainer.

### D2 — `seekable_members` vs `open_stream(seekable=)` *(→ Q16)*

- **#231:** a live pre-freeze vocabulary question — rename, alias, or accept.
- **#230:** already **decided**, citing `archive-reading` §"Declared member-stream
  capabilities": *"`open_stream` SHALL keep its `seekable: bool` parameter, and both
  entry points SHALL use the same `seekable` vocabulary for the same concept."*

**Merged verdict: #230's reading, with the citation.** The spec text is explicit and the
code matches it. Q16 exists only so the maintainer can confirm the spec still says what
they want; it is not an open design question. *(The real problem with the flag is F1,
not its name.)*

**Round 2 (O3) — merged verdict upheld, for a better reason.** The maintainer initially
overruled this and reopened it; two outside reviews then converged back on "keep both
names." More usefully, they reframed the question from naming to **placement** — should
it be `archive.open(member, seekable=True)`? — and answered *keep it per-archive*,
because declaring seekability drives open-time work (index construction, accelerator
selection). And the deadline dissolves: a per-`open()` flag is **purely additive** later,
so nothing is foreclosed by deciding now. The spec citation above stands unedited, but
now because it is right rather than because it is there.

### D3 — the RTL clause: spec fiction, or a vague clause? *(→ F10 / Q10)*

- **#231:** spec fiction — `testing-contract` says "warns **or rejects**" and the code
  only warns.
- **#230 (this merge):** not fiction. The clause is a **disjunction**, and the code
  implements one branch of it, so no requirement goes unperformed. It is *vague* — a
  reader cannot tell which outcome to expect — which is a real but different defect.

**Merged verdict:** tighten the clause (agreeing with #231's fix), but the finding
worth ranking is the one underneath it: the bidi warning is the library's **only
advisory with no `DiagnosticCode`**, which is the `VISION.md` warnings-as-data gap
verbatim. F10 is filed that way.

**Round 2 (O7) — the clause tightens to *both* branches, split by character class.**
Reject **overrides and isolates** (U+202A–202E, U+2066–2069) in safe extraction; keep the
**directional marks** (U+061C, U+200E, U+200F) warn-only, because those occur in
legitimate Arabic and Hebrew filenames and do not reorder surrounding text. Note for
whoever implements it: the reject set must be written out explicitly — the library's
existing `_BIDI_CONTROLS` (`src/archivey/internal/naming.py:32`) is *broader* than the
override set and reusing it would break legitimate RTL names.

*(A fourth, minor: #231 wants the `rar_unrar` `RuntimeError` mapped defensively; #230
recorded it as unreachable and not worth a change. Both are defensible — Q15, cheap
either way.)*

---

## Where the divergences cluster

1. **A capability flag or source shape gating the wrong thing** (F1, F5, F8). Each got
   here honestly by reusing machinery that happened to be nearby — the seek index, the
   `Path` branch, the backend class attribute. In every case the correct behaviour
   already exists a few lines away (gzip's trailer peek, `_with_seekable_source`, the
   `SUPPORTS_PASSWORD` gate).
2. **Explicit caller input discarded rather than refused** (F2, F7). #225/P8 decided
   this for directory `format=` and the rule was never generalized. Two instances remain,
   and one generalization covers both.
3. **Error typing that stops at the entry point** (F3, F4, F15). The base reader's
   translation boundary is sound; the holes are *outside* it — `resolve_source` runs
   before any translator exists, and ZIP's blanket `ValueError` arm catches a lifecycle
   fault on the way past.

Clusters 2 and 3 each have a single cheap generalization available before the tag.

---

## What is actually fine

Recorded so the next review does not re-derive it. Both passes checked most of these; a
seed that resolved to a non-issue is a finding.

### Seeds that resolved to "fine"

| Seed | Verdict | Found by |
|---|---|---|
| **A4 pipe / non-seekable matrix** | **Fine.** Every trailing-index format (ZIP, ISO, 7z, RAR) refuses a pipe with one `StreamNotSeekableError` and one message shape, from both `open_archive` and `extract()`; TAR and every single-file codec accept one. No soft failures. Residuals: queryability (F8) and the *sequence* form (F3). | both |
| **A1 password / open laziness** | **Fine, bounded.** No sibling of the #225 eager-work bug: ZipCrypto confirms at member open, 7z folder passwords at first member of the folder, RAR data at read. The bound is F9 — header encryption is not lazy and cannot be. | both |
| **A2 Path vs seekable `BinaryIO` gates** | **Fine except F5.** All other `isinstance(…, Path)` sites are legitimate open-affordances (independent FDs, `open` vs `open_fp`, Path-always-seekable capability, directory format law). `compressed_size` is the one residual. | 231 |
| **A6 stored digest / `hashes` emptiness** | **Fine.** ZIP `crc32`; 7z `crc32`; RAR `crc32` (+`blake2sp` when present); TAR/ISO/directory empty (no stored digest); `zip-aes` empty (WinZip AE-2 zeroes the CRC field — format law); zlib omitted with a stated reason. The lzip/xz rows are F1, not an emptiness bug. | both |
| **A7 duplicate-name / `is_current`** | **Fine.** `_apply_last_entry_wins_is_current` is the single driver; unique names keep the backend flag so RAR `path;N` history survives; guide story matches. | both |
| **A8 cost-receipt honesty** | **Fine.** Every specced example row reproduces (ZIP `INDEXED`+`DIRECT`; plain TAR `REQUIRES_SCANNING`+`DIRECT`; `.tar.gz` `REQUIRES_DECOMPRESSION`+`SOLID`; solid 7z `INDEXED`+`SOLID`). `notes` empty everywhere — never an occurrence log. | both |
| **B4 error-translation consistency** | **Fine inside the boundary.** No raw exception crossed the public boundary in ~1000 probe calls except the ones the spec mandates (`TypeError` for `len`/`in`, `io.UnsupportedOperation` for `seek`, `ValueError` for closed-stream I/O, `OSError` unchanged). The holes are F3/F4/F15, all *outside* the base-reader translator. | both |
| **C2 extras naming vs capability** | **Fine.** Hints come from `MissingComponent.install_hint` per component; live hints are `[recommended]` / `[seekable]`. No `install archivey[7z]` anywhere in `src/`. | both |
| **C4 exception roots (ADR 0012)** | **Fine, not reopened.** No call site puts an error on the wrong side of the tree. | 230 |
| **C5 CLI import paths** | **Isolated**, exactly as the brief predicted — `ExtractionProgress` is the only `internal/` import in the CLI, and it is already public (F14). | both |
| **D1 pass-driver / member-list leftovers** | **No regression.** Single `_drive_pass_streams` / `_materialize_members`; no post-#184 third copy or backend-local bypass. | both |
| **D2 reader close vs stream lifetime** | **Settled and uniform.** `reader.close()` closes member streams on all 24 measured keys; later stream read → `ValueError` (stdlib shape); later reader op → `ArchiveyUsageError`. | both |
| **CLI reserved knobs** | **Fine.** `--salvage` and reserved verbs fail loudly; `--track-io` on a reader without counters prints "unavailable". No silent CLI no-ops. | 231 |
| **Format-scoped config knobs** | **Fine by construction.** `strict_archive_eof` on non-TAR and `zip_unflagged_fallback_encoding` on non-ZIP are inert because they name a thing the format lacks — no caller assertion is being overruled. *(F7 is the case where `strict_archive_eof`'s inertness bites.)* | both |
| **TAR corrupt final header (RA vs streaming)** | **Format-law residual**, already asserted in the product suite; `open-issues` P3 (native TAR walker) owns the follow-on. Not an accident to delete before the tag. | both |
| **Wrong-password shapes across formats** | **Format law.** 7z AES+store can yield garbage plus `DIGEST_UNVERIFIABLE`; RAR raises `EncryptionError`; ZipCrypto confirms. Keep as `formats.md` rows; do not force one exception type. | both |

### Spec clauses that are honest

The O-23 sweep over **landed** capabilities (Phase 8 `seekable-gzip-and-block-writing`
and Phase 9 `archive-writing` excluded per the §B carve-out) found the class largely
paid off by #225. Every advisory clause maps to a real emission:
`MEMBER_NAME_ENCODING_INFERRED`, `FORMAT_EXTENSION_CONFLICT`, `DIGEST_UNVERIFIABLE`,
`STREAM_REWIND_REDECOMPRESSES`, `SEEK_INDEX_DEGRADED`. `archive-reading`'s "no
diagnostic, no warning" for a solid random `open()` is correctly **absent** — there is
no `warnings.warn` anywhere in `src/`.

Two clauses did not survive: the **XZ** size row (no seekability condition, but the code
has one — F1's spec half) and the **RTL** clause (permissive to the point of
uninformative — F10).

---

## Deliverables map

| File | What it is |
|---|---|
| `SUMMARY.md` | this file — merged findings, corrections, disagreements |
| `expected.md` | the matrix's **expected** column, written from `VISION.md` + specs alone **before** the probe ran, with its contamination disclosure |
| `parity-matrix.md` | expected vs observed, the diff, and the O-21 trace per divergence |
| `silent-exceptions.md` | argument-discard / spec-honesty / error-translation sweeps |
| `vocabulary.md` | surface-vocabulary leftovers that freeze at the tag |
| [`open-questions-for-discussion.md`](open-questions-for-discussion.md) | **Shareable standalone brief** — every still-open item written for a reader with no prior context; safe to circulate outside the project |
| `q13-rewind-diagnostic.md` | Q13 worked through: the resolution, three drafts (docstring / O-23 reframe + 14-code audit / spec note), and **F19** |
| `QUESTIONS.md` | 16 maintainer decisions — **all ruled on**, each with severity, evidence, fix vehicle, and the decision recorded inline; plus the pay list re-ranked against them |
| `repro/probe_matrix.py` | the generator — runs the whole matrix by execution |
| `repro/matrix.md`, `repro/matrix.json` | its output at this commit |
| `tests/test_review_simplicity_consistency.py` | **54 guardrails + 15 strict-xfail red halves**, in `tests/` so CI runs them |

## Baseline (this environment, `[all]` config)

| Check | Result |
|---|---|
| `pytest` (before this review) | 2132 passed, 65 skipped, 3 deselected — 87% coverage |
| `pytest` (with the review's guardrails) | **2186 passed, 65 skipped, 15 xfailed** |
| `ruff check` / `ruff format --check` | clean |
| `pyrefly check` | 0 errors |
| `ty check` | clean |
| `openspec validate --all` | 25 passed, 0 failed |
| `format_availability()` | every known format `FULL` |
| Corpus coverage | 24 of 25 format keys via the corpus; RAR via committed fixtures (F16) |

Three-config runs were not needed: no finding depends on an optional library's presence
or version — all reproduce with the stdlib codecs plus `[recommended]`.

## Not covered

- **RAR corpus shapes** (F16) — reading is exercised via fixtures; the 41 declarative
  corpus cases are not.
- **Multi-volume** joins beyond the entry-point refusals in F3 — no corpus builds a
  volume set here.
- **Free-threaded / concurrent** rows — `reader-concurrency` was treated as settled
  ground (`brief.md` §E); only the single-live-stream gate was probed. **This gap is now
  load-bearing:** O2c (decoder reuse under concurrent members) is the review's one
  unanswered question and nothing in it is backed by a measurement. The cheap first step
  named there — check whether backends declaring `concurrent_members` already materialize
  member data rather than holding N live decoders — would close the gap for this purpose.
- **Damaged-input read salvage** — `VISION.md` records it as a known gap and `IDEAS.md`
  owns it; only the *listing* honesty contract was checked, and it holds.
