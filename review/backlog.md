# Review backlog — deferred review ideas

Non-security review topics worth doing, but *after* the remaining in-flight
round (`debt-ledger`, `performance`). They differ in character and timing:

- **Topics 4 + 5** (test-strategy, structural-cleanliness) — **done**, archived as
  `archive/2026-07-28-debt-ledger/` (whole pay list paid; T7 + T4 half closed it on
  2026-07-28; remaining items are explicit KEEPs).
- **Topic 6** (decode-engine performance) — a later *performance* round, once the
  `stream-layering` wrapper work has landed; mostly independent of it.
  *(stream-layering fusion landed in #137 — Topic 6 is unblocked on that axis.
  Absorb when commissioning: (1) parked stream-layering **Q4** — real
  `SlicingStream.readinto` under lock so a fused hasher can hash a `memoryview`
  without a bytes copy; park until an extract path is shown `readinto`-bound;
  (2) optional cleanup — delete the thin leftover `VerifyingStream` wrapper once
  nothing but unit tests / `codecs.py` length backstops need it. *Note (2026-07-20):
  `_GzipTruncationCheckStream` remains after `rapidgzip-truncation-investigation`
  (empty→stdlib + ISIZE); container `VerifyingStream` and unit tests still use the
  wrapper — Topic 6 delete-when-unused stays parked.*)
- **Topic 7** (outside-in adoption / confidence) — a **capstone**, meaningful only
  *after everything else is fully addressed*; it judges the finished library, not a
  work in progress.
- **Topic 8** (documentation *content*) — accuracy / gaps / quality of the prose, after
  the in-flight docs IA review has settled where each page lives. **Commissioned
  2026-08-15** as [`docs-content/brief.md`](docs-content/brief.md).
- **Topic 9** (simplicity & consistency) — **done**, archived as
  [`archive/2026-08-15-simplicity-consistency/`](archive/2026-08-15-simplicity-consistency/)
  (findings #230/#231; W1–W9 paid in #232; Q13 expansions #233–#236).
  **O2b/O2c** (solid decoder hold under concurrency) parked → `IDEAS.md`.
- **Topic 10** (the problem catalogue) — **commissioned 2026-08-15** as
  [`problem-catalogue/brief.md`](problem-catalogue/brief.md). Not a review; runs in
  parallel with Topic 8 on disjoint sources.

**Topic numbers are IDs, not an order.** Current intended sequence: docs IA (in
flight) → **Topic 8** ∥ **Topic 10** → **Topic 6** → **Topic 7** last. See
[`STATUS.md`](STATUS.md).

## Parked from archived deep reviews (2026-07 / 2026-08)

Items consciously deferred when archiving deep reviews. Do not re-open those
review directories for these.

| Item | Origin | Where it lives now |
|------|--------|--------------------|
| Library `verify` / `VerifyReport` (E2 / **Q5**) | api-coherence | `IDEAS.md` — deferred past 0.2.0 |
| Stored stream digests (zlib Adler omit + lzip multi-member CRC) | api-coherence Q6 fill | **Done** #160 — archived OpenSpec `archive/2026-07-19-surface-stored-stream-digests` |
| CLI list marks for `ANTI` / non-current (**D1**) | api-coherence → cli-product | **Done** in `archive/2026-07-20-cli-product/` |
| CLI **P4** `--json` (wait for `hash` / member schema) | cli-product Q2 | `IDEAS.md` (CLI follow-ups) / debt-ledger DD7 |
| CLI **Q4** `--raw` / TTY-only quoting remainder | cli-product Q4 | debt-ledger DD8 (additive; recommended style already applied) |
| `SlicingStream.readinto` (**Q4**) + optional `VerifyingStream` delete | stream-layering | Topic 6 adjacency above |
| Solid-block decoder hold across `open()` + concurrency (**O2b/O2c**) | simplicity-consistency | `IDEAS.md` §Performance |

The **docs full review** is in flight as of 2026-07-29 ([`docs/brief.md`](docs/brief.md)) —
an information-architecture pass separating user / contributor / design-record / history,
commissioned ahead of Topic 7 because it decides where docs *live*, not whether they
persuade. Topic 9 is archived; library surprises found while writing guide prose become
fix PRs. [`STATUS.md`](STATUS.md) records the current ordering.

When commissioned, each gets its own top-level directory with a `brief.md` and
archives when addressed (see `README.md`).

## The framing: zero tech debt

The maintainer's goal for this project is to keep it **debt-free** — not "clean
enough," but *zero* deliberately-carried debt. That reframes a "cleanliness" review
from cosmetic to a **debt ledger**: enumerate every known shortcut, duplication,
drift, and deferred decision, and for each one force the choice — *pay it now
(before the public API freezes) or record it as an explicit, justified decision.* The
deliverable is that ledger with a pay/keep verdict per item, not a vibe.

The value of doing this *before* `0.2.0` is specific: after the release, some of
these (public-surface, spec) stop being free to change.

## Topic 4 — Test-suite strategy / coverage architecture

**Why:** all three security reviews repeatedly concluded *"no test in the suite
catches this."* That's a signal about the test *strategy*, not three isolated gaps. A
meta-review of how the suite is built — where example-based tests should be property
or fuzz tests, whether the declarative corpus covers the format×codec×config matrix,
where fault injection is thin — is due; the old `archive/2026-07-12-codebase-deep-review/tests.md`
predates +5.5k LOC, native RAR, native ZIP codecs, and the CLI.

Concrete already-known gaps to fold in (don't re-derive):
- **No randomized/property seek test** — archived stream-decoder **F5** (also old
  review finding #6). Every seek test reads forward-to-EOF before seeking, the one
  ordering that hid the F1 crash. A seek-math property test would have caught it.
- **No "truncated read through both `read(-1)` and chunked idioms" test** — stream
  **F4** root cause; the deferred-error path is only exercised one way.
- Free-threaded coverage runs core-only; the ISO/accelerator support boundary is
  implicit in a CI flag (old `roadmap.md`).
- Oracle retirement (#46) fallout — is the declarative corpus now the sole guard, and
  is its matrix complete for the formats added since?

## Topic 5 — Structural cleanliness (the debt ledger)

**Why:** the old `deep-simplification` pass proposed three category-deleting
structural changes and they were **deferred, not rejected**:
- **S1 — one error boundary.** `_translated_errors` was applied to the original
  backends but S1's full "backends never hand-roll translate/stamp/raise" was left;
  check whether RAR (which routes through the shared boundary — good) and the newer
  paths kept it honest, and whether the ~10-sites duplication is actually gone.
- **S2 — one member-list pipeline** (materialized + progressive unified). Deferred.
- **S3 — one pass driver.** Deferred — and S3 *explicitly predicted* that the native
  RAR reader would add a fourth copy of the "close-previous / open-current / yield /
  cleanup" loop. **RAR has now landed**, so this duplication is concrete and
  measurable today rather than hypothetical. This is the single best-motivated item
  in the ledger.

Plus the mechanical debt a zero-debt pass should sweep:
- Module-split coherence after ~25 archived OpenSpec changes (`internal/config` vs
  `config`, `extraction_types`, `sevenzip_methods`/`pipeline`, `timestamps`) — is each
  split earning its seam?
- Dead code / unused exports (overlaps `api-coherence`'s surface audit — coordinate).
- **Doc ↔ spec ↔ code drift**: with ~25 changes archived and specs synced repeatedly,
  are the user docs, the OpenSpec live specs, and the code still telling one story?
  (The old review found docstring/spec mismatches; the surface has churned a lot since.)
- Any remaining `TODO`/`FIXME`/"deferred"/"follow-up" markers in `src/` — each is a
  debt-ledger line by definition.

## Topic 6 — Decode-engine performance (`DecompressorStream` / `Decoder`)

**Why:** the archived stream-decoder review (PR #122) and the #96 composition refactor
looked at the decode engine for **correctness and clarity**, not performance — and the
in-flight `stream-layering/` review deliberately scopes the decode engine *out*, owning
only the wrapper stack (slice/verify/outer) around it. So the per-chunk cost of the
decode engine itself is unreviewed. Mostly independent of `stream-layering`, so it's a
separate later performance round (after that one lands, so the two don't churn the same
code at once).

Concrete surfaces to measure:
- Per-`read()` dispatch through `DecompressorStream` → `Decoder` and the base's
  `_read_decompressed_chunk` buffering (the archived F3 memory-bound fix touched this —
  is the *steady-state* read cost tight now?).
- `fix_stream_start_position` adding a **second** `SlicingStream` in front of a codec that
  assumes `tell()==0` — is that slice avoidable on the common path?
- The accelerator wrappers (`_AcceleratorStream` / `_GzipTruncationCheckStream`) per-chunk
  overhead vs the raw rapidgzip handle, and whether the AUTO gate's crossover is where the
  fused cost actually breaks even (coordinate with `performance/`'s gate findings).
- `readinto` zero-copy through the decode stack (same lens as `stream-layering`, one layer
  down): does decoded output get copied more than necessary?

Not a re-litigation of the #96 design — a pure "is the decode read path as cheap as it can
be" pass, with numbers.

## Topic 7 — Outside-in: adoption & confidence (capstone)

**Why:** every other review looks *inward* (is this code correct / clean / fast). This one
looks *from the outside*: would someone actually adopt archivey, and what's missing to make
them confident? Run it **last** — it judges the finished library against its competitors and
its own VISION promises, so it's only meaningful once the correctness/API/perf/CLI work is
fully addressed. Two framings, usable together:

- **The adopting engineer / company (primary).** Put the reviewer in the shoes of an
  external engineer evaluating archivey against the alternatives (`zipfile`/`tarfile` +
  ad-hoc glue, `libarchive` bindings, `py7zr`/`rarfile`, `patool`, shelling out to
  `7z`/`unrar`). What's missing for **confidence and peace of mind** to depend on it: API
  stability guarantees and semver, the security/CVE-surface story made legible, a
  trustworthy changelog/release cadence, benchmarks a skeptic can rerun, documentation that
  answers "how do I do X safely," licensing/provenance of vendored code, supported-platform
  and free-threading matrices, "what happens on damaged/hostile input" stated plainly,
  responsiveness signals. Deliverable: the concrete gaps between "technically excellent" and
  "a stranger bets a production pipeline on it," ranked.
- **The CPython maintainers (a high bar, not a goal).** As an explicit stretch lens — *not*
  an actual objective — assess it as if stdlib inclusion were on the table: API taste and
  minimalism, zero-surprise cross-platform behaviour, test rigor, security posture,
  maintenance burden, backwards-compat discipline, the "does this belong in the standard
  library" bar. Useful precisely because it's a harsher standard than any real adopter would
  apply, so it surfaces polish gaps the primary framing might accept.

This is judgement + gap analysis, not a bug hunt — closer to a product/positioning audit
grounded in the code and docs. It likely produces roadmap items, not fixes.

## Topic 8 — Documentation *content* (after the IA pass) — **COMMISSIONED**

**Commissioned 2026-08-15** as [`docs-content/brief.md`](docs-content/brief.md), at
`d4668c3` — once the library churn the prose was waiting on had landed (`#225`,
`#232`, `#233`–`#236`). The brief carries the measured surface, the seed list, and the
four-pass ranking below. What follows is the original sketch, kept as provenance.

**Why separate from the docs IA review** (`docs/brief.md`, in flight): that one decides
where prose *lives*; this one judges whether the prose is *right*. Keeping them apart is
a reviewability argument, not a tidiness one — a `git mv`-only migration diff can be
checked by inspection, a move-plus-rewrite diff cannot. It is also cheaper: polishing a
page that is about to be merged or deleted is wasted work, and "is this page complete?"
is unanswerable until the page's scope is settled.

**Head start:** the IA audit reads every file anyway and records what it notices in
`review/docs/observations.md` without acting on it. Start there rather than from zero.

Three distinct passes hide under "content review". They have different value and are
worth ranking rather than bundling:

1. **Accuracy vs the code (highest value).** Does each page still describe what the code
   does? The surface has churned hard — native 7z/RAR, the CLI, verify fusion, extraction
   policies, diagnostics. Past debt-ledger passes found docstring/spec mismatches; the
   user guide has never had a systematic pass. Mechanically checkable in places: every
   code block in `docs/` should run, and several would make good doctests. **Also:**
   rewrite the Gotchas accelerator “don’t close source” bullet for `_TrappingSource`
   (D9); triage `dev-docs/known-issues.md` into resolved / mitigated / upstream /
   fixable / evidence (D9 — required follow-up, not optional).
2. **Gaps.** What does an adopting engineer look for and not find? Overlaps Topic 7 —
   coordinate: Topic 7 judges whether the docs *persuade*, this asks whether they
   *answer*. If Topic 7 has already run, take its gap list as input.
3. **Register and concision — promoted, not polish.** The IA migration moved prose from
   a threat model, an ADR and the specs onto user pages. It is accurate and it reads
   like the documents it came from. The audience is a working developer who is *not* an
   archive-format specialist, and several pages currently assume otherwise. Rules and
   the worked example are in `review/docs/observations.md` **O-17**; the accuracy half
   of the same problem is **O-16**. This is no longer "do it last if there is time" —
   the material that most needs it is the material carrying the safety claims.
4. **Remaining quality: examples, structure within a page.** The genuinely
   bikeshed-prone part. Do it last, and only where a page is load-bearing (`index`,
   `safe-extraction`, `formats`, `opening-and-listing`, `reading-members`).

**Sequencing.** After the IA migration lands, before Topic 7 ideally — so the capstone
judges docs that are both correctly filed and correct. If time forces a choice, do (1)
alone: an inaccurate doc is a bug, an unpolished one is not.

**Guardrail to consider while doing it:** executable examples (doctest or a tested
snippets file) turn accuracy from a recurring manual review into a CI failure. That is
the difference between doing this pass once and doing it every release.

## Topic 9 — Simplicity & consistency (behavioural uniformity) — **DONE**

**Archived 2026-08-15** as
[`archive/2026-08-15-simplicity-consistency/`](archive/2026-08-15-simplicity-consistency/).
Findings from two independent passes (#230/#231); pay list W1–W9 implemented in
#232; Q13/O-23 expansions in #233–#236. The only deliberate park is **O2b/O2c**
(hold solid-block decoder across `open()`, concurrency lifetime) →
[`IDEAS.md`](../dev-docs/IDEAS.md) §Performance.

Brief and seeds (historical):
[`archive/2026-08-15-simplicity-consistency/brief.md`](archive/2026-08-15-simplicity-consistency/brief.md).

## Topic 10 — The problem catalogue — **COMMISSIONED**

**Commissioned 2026-08-15** as [`problem-catalogue/brief.md`](problem-catalogue/brief.md),
at `d4668c3`. **Not a review** — an extraction and normalization pass over what the
project already knows: one entry per non-trivial problem archivey has had to solve
(format quirks, upstream library defects, security hazards, platform traps, usage
patterns), stated so that someone who has never seen archivey could design against them.

The material is already written across ~180 documents — 72 archived change proposals with
a `## Why`, 57 `design.md` files, 17 ADRs, 11 review summaries, 8 investigations, the
threat model, `known-issues.md`, `library-analysis.md`, and `dev-docs/history/`. Nothing
collects them, so one problem is restated in three vocabularies and cannot be counted.

**Problems, not decisions.** Each entry links to the decision that resolved it, in a
separate strippable field — because the catalogue's second consumer is a **fresh-design
comparison**: hand a frontier model the problems alone, ask for an architecture, and
compare. That only works if the problems are stated without our vocabulary, so
"how do we avoid copying bytes twice in the decoder stack" fails the test and
"verifying a checksum and delivering bytes are the same read" passes.

Runs **in parallel with Topic 8** — disjoint sources (that one reads `docs/` and `src/`,
this one `dev-docs/` and the archives) — and takes the code-comment residue from Topic 8's
capability workers, which is the only way to reach problems that never entered a register.

## Not a review — a feature gap to track separately

**Salvage / best-effort read mode** (old `roadmap.md`, `IDEAS.md`) — the "founding use
case" (truncated archive → every recoverable member + an honest error) is still
unbuilt; reads are all-or-error. This is a feature to *design and propose* (an
OpenSpec change), not something a review finds. Flagged here only so it doesn't get
lost among the review topics — it likely outranks both topics above in product value,
and `--salvage` is already reserved in the CLI grammar waiting for it.

**Partial members + honest error accessor (api-coherence Q7)** — **done** in
#157 / archived OpenSpec `partial-members-and-errors`
(`members_report()` → `MemberListReport`, complete-or-raise
`members()`/`scan_members()`, RA yield-then-raise). Salvage / best-effort
resync past damage remains a separate feature gap above.
