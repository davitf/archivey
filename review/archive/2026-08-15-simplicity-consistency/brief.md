# Brief — simplicity & consistency pass

Commissioned 2026-08-06 against `main` @ `a1cbf2a` (post-#225 library fixes from
the docs review). Prompted by the docs cleanup: writing user-facing prose keeps
turning up **weird behaviours and format differences that should not be there** —
some of which were bugs, some accidental special cases, some specs describing
behaviour that never shipped.

## The ask

Produce a **focus map and ranked investigation plan** for a simplicity and
consistency pass over the library — not a full rewrite, not a second debt ledger,
not Topic 8 (docs content). The question is:

> Where does the library still surprise a careful reader because two formats, two
> entry points, or two layers disagree about the same concept — and which of those
> surprises are accidents we should delete before `0.2.0` freezes them?

Deliverable shape per `review/README.md`: `SUMMARY.md` (headline + ranked focus
areas), theme files as useful, `QUESTIONS.md` for maintainer decisions, and a
"**what is actually fine**" section so settled ground is not re-opened. This brief
already carries a **light recon** (§Known seeds); the review deepens and verifies
those, then expands — the seeds are a floor, not the job, and §Known seeds says how
to keep them from becoming the whole pass.

**Analysis-first, not artifact-free.** Propose and get decisions before changing any
library behaviour. When a finding is `CONFIRMED` with a runnable repro, flag it as a
red–green candidate — do not fix it in this review. **Do** commit the probe scripts
and the assertions that pin today's behaviour: those are evidence and guardrail, not
fixes (see Hard constraints).

## Why now

1. **The docs cleanup is already the best bug-finder the project has right now.**
   Writing `opening-and-listing` / `reading-members` / Gotchas produced four library
   fixes in #225 (unimplemented solid warning in the spec, silent `format=` on a
   directory, Path-gated size/CRC probes, deferred password confirmation on solid
   7z/RAR). Three of those were invisible to `openspec validate --strict`. The
   method that found them — *for each behavioural claim, find the line that
   implements it and check the branch where it does not hold* (O-21) — is the
   method this review should run across the uniform-interface surface, not just
   the pages being rewritten.
2. **`0.2.0` freezes accidental complexity.** After the tag, "ZIP does X but TAR
   does Y for the same call" becomes a compatibility tax or a permanent Gotchas
   bullet. Accidents that can still be deleted should be found while deletion is
   free.
3. **Prior reviews covered adjacent ground and left this gap.**
   `api-coherence` judged public surface shape and member-model parity at freeze
   (P1 duplicate names fixed). `debt-ledger` paid structural debt (S2/S3 pass
   driver unified). Neither was commissioned as *"walk every observable and ask
   whether format/entry-point differences are format law or accretion."* The docs
   work shows that class is still live.

## Values (tie-breakers)

`VISION.md` is the product authority; it is not a checklist to rubber-stamp.
Rank findings against these — and when they conflict, prefer the senior-engineer
reading below.

### From VISION (load-bearing)

1. **One uniform interface** with honest cost / capability signals — behaviour
   differences between formats are **data** (`None`, explicit fields, diagnostics,
   `CostReceipt`), never silent guesses.
2. **No surprises** — a logging warning most apps never see is a surprise deferred,
   not avoided; prefer queryable data over ambient warnings.
3. **Safe by default** and **memory-safe parsing of hostile input** — outrank
   polish when a consistency "fix" would weaken either.
4. **Content-first** — reading/streaming/metadata before extraction; extraction
   footguns still matter when they diverge by format.
5. **Conservative public surface** — easy to keep stable under one-maintainer +
   agents maintenance reality.

### Senior-engineer addenda (not in VISION, but apply here)

These are the values that make a "simplicity and consistency" pass different from
a feature review:

- **Predictability beats cleverness.** One rule with a documented, format-forced
  exception beats three special cases that each made sense at the time.
- **Prefer deleting the quirk to documenting it** when the quirk is not format
  law, stdlib constraint, or an explicit ADR. Documenting an accident permanently
  raises the docs tax (the docs review is paying that tax now).
- **Same concept → same spelling, same failure mode, same emptiness contract.**
  `open_archive` vs `open_stream`, library vs CLI, Path vs seekable `BinaryIO`,
  random-access vs streaming — divergence needs a reason a stranger can find
  without reading the implementation.
- **Spec honesty over aspirational prose.** A requirement that describes
  unimplemented behaviour is a defect (O-23). Softening the user guide to match
  buggy code is the wrong half of the time (O-26) — check the spec before
  rewriting the sentence; three outcomes, only one is a docs fix.
- **Do not invent abstractions to paper over inconsistency.** If two backends
  disagree, the first question is which one is wrong — not which helper would
  hide both. Structural unification (S2/S3-class) is in scope only when it
  *deletes a recurring failure mode*; line-count vanity is out.
- **Maintenance cost compounds.** Every format-conditional in the guide, every
  diverging error message, every dual vocabulary is paid on every future change
  and every agent session. Small consistency wins compound under AI-assisted
  maintenance.

## What this is / is not

| This review | Not this review |
|---|---|
| Cross-format / cross-entry-point **behavioural** consistency | Security / hostile-input (archived 2026-07 security round) |
| Accidental complexity that docs or callers must special-case | Topic 8 docs *content* rewrite (accuracy/register) — hand content findings to it |
| Spec ↔ code honesty on shipped claims | Topic 6 decode-engine perf |
| Light structural simplicity only where it deletes a failure class, plus how many concepts the common task costs a caller | Re-litigating debt-ledger KEEPs or settled ADRs |
| Ranking what to fix before `0.2.0` vs accept as format law | Topic 7 outside-in adoption capstone |
| Public-surface vocabulary leftovers that freeze at release | Feature work (salvage mode, native ZIP, multi-volume ZIP) |

## The surface being reviewed (measured at `a1cbf2a`)

So the reviewer can size the job and the maintainer can size the ask. None of these
are targets — they are the denominators the deliverable is measured against.

| Axis | Count | Where |
|---|---:|---|
| Backend readers | 7 | `src/archivey/internal/backends/*_reader.py` (directory, ISO, RAR, 7z, single-file, TAR, ZIP) |
| Format variants in the declarative corpus | 25 | `tests/sample_archives.py` `FORMAT_KEYS` — the matrix's real column count, since `tar.*` variants behave differently from `tar` |
| Corpus entries (archives × those formats) | 20 | `tests/sample_archives.py` `CORPUS` |
| Public API surface | 82 | `archivey.__all__` — every name that freezes at the tag |
| Capability specs | 24 | `openspec/specs/` — the spec-honesty sweep's domain, minus unlanded phases (§B carve-out) |
| `isinstance(…, Path)` sites | 27 | `src/archivey/` — sizes the seed A2 sweep exactly |

**Definition of done for the matrix:** every cell filled or `N/A`-with-reason, with each
cell marked *observed* / *read* / *unmeasured* (§Suggested process step 2). "I ran out of
budget" is a legitimate outcome — an unfinished matrix that says which cells are
unfinished is useful; one that silently omits them is not.

## Known seeds (light recon — deepen, do not re-derive from zero)

These came from the docs cleanup observations, `#225`, `open-issues.md`,
`code-self-documentation.md` / `api-surface-suggestions.md`, and a short read of
backends + `core.py` at `a1cbf2a`. Each is a **starting lead**, confidence as tagged.

> **The seeds are a floor, not the job.** A pre-built list anchors: the temptation is
> to confirm the eight rows in §A and call the pass done, which would answer "is this
> format law or accretion?" only where someone already suspected an answer. Budget the
> seeds at **roughly half the pass**; the observable matrix (§Suggested process step 2)
> gets walked completely, including rows no seed points at. A seed that turns out to be
> a non-issue is a finding too — record it under "what is actually fine" so the next
> review does not re-derive it.
>
> **Counterweight — scheduled as the matrix's *expected* column** (§Suggested process
> step 2). Once the matrix rows exist but before the probe runs, write what each row
> *should* do from `VISION.md` and `openspec/specs/` **alone** — no seeds, no code —
> then diff that against observed behaviour. Keep it to the matrix rows; this is not a
> second design proposal. Weight the **disagreements** heavily and the agreements
> lightly (shared priors make convergence weak evidence — the same caveat
> `review/docs/independent-brief.md` records). The docs review's equivalent pass
> produced its strongest phase-1 finding this way; this one is deliberately a fraction
> of the size, and it is what keeps the seed list from deciding in advance where to
> look.

### A. Uniform-interface / format-difference class (highest value)

| Seed | Signal | Confidence |
|---|---|---|
| **Password / open laziness on solid archives** | Just fixed for 7z folder-pipeline and solid RAR (`#225` / O-26). Ask: are there sibling "work happens earlier than the docs promise" sites — ISO open, ZIP ZipCrypto confirm, encrypted header paths, progressive listing? | `PLAUSIBLE` residual |
| **Path vs seekable `BinaryIO` gates** | Size/CRC probes were Path-gated while specs said seekability (`#225` / O-25). Sweep remaining `isinstance(..., Path)` / "path sources only" branches in readers and codecs. | `PLAUSIBLE` |
| **Streaming vs random-access honesty asymmetries** | TAR: corrupt *final* header caught in RA, not in forward-only streaming (`formats.md`, open-issues P3). Other formats: does "streaming mode" change which errors exist, or only when they fire? | `CONFIRMED` (TAR); others unchecked |
| **Pipe / non-seekable matrix** | `open_archive` refuses pipes in RA; `extract()` auto-streams; ZIP/ISO/7z still cannot stream from a pipe even with `streaming=True` (`must-explain` #4). Is every refusal loud and uniform, or are there soft failures? | Partial |
| **Encryption / wrong-password shapes** | 7z AES+store can yield garbage + `DIGEST_UNVERIFIABLE`; RAR raises `EncryptionError`; ZipCrypto confirmation cost differs for STORED. Are the *caller-visible* contracts parallel where the format allows, and explicit data where it does not? | Needs matrix |
| **Stored digest / `member.hashes` emptiness** | api-coherence called hashes "fine and documented"; docs writing still trips on conditional fill (lzip/gzip/xz). Re-check the matrix against code after `#225` single-file fix — one row per format, one emptiness rule. | Re-verify |
| **Duplicate-name / `is_current` residual** | P1 fixed (`_apply_last_entry_wins_is_current`). Confirm RAR `path;n` history naming vs ZIP/TAR last-wins still tells one story in the guide and the conformance sweep. | Likely fine; spot-check |
| **Cost receipt honesty** | Listing/access costs declared per backend. Any remaining docstring/impl skew (historical P2 was RAR `INDEXED`)? Directory / single-file / compressed-TAR still the tricky rows. | Spot-check |

### B. Silent exceptions / "one place does something different"

| Seed | Signal | Confidence |
|---|---|---|
| **Spec promises without code** | Solid re-decode *warning* was specified and never implemented (O-23). Sweep live specs for "may warn" / "SHOULD emit" / scenario outcomes that no test asserts. **Scope: landed phases only** — see the carve-out below. | Method proven |
| **Silent argument discard** | Directory `format=` was the only explicit assertion overruled without error (`#225` / P8). Analogues: ignored config knobs, CLI flags that no-op on some formats, selector fields backends skip. | Method proven |
| **Diagnostics boundary** | New rule: diagnostics describe the **archive**, not the caller's usage (O-23). `STREAM_REWIND_REDECOMPRESSES` sits awkwardly on the usage side — flag only, do not churn unless the review finds a cleaner cut. Whether to emit a plain `warnings.warn` on solid random open was **left explicitly undecided** by the maintainer in O-23 (`review/docs/observations.md`) — there is no such call in `src/` today; decide it or record it as still open. | Decided rule; one awkward code |
| **Error translation consistency** | S1 boundary exists; spot-check whether newer paths (RAR unrar map, 7z pipeline, single-file, extract coordinator) still hand-roll translate/stamp or leak raw `ValueError` / `RuntimeError` / `NotImplementedError` across the public boundary. | Light grep shows ZIP still has careful tuples — verify others |

**Spec-honesty carve-out — sweep landed phases only.** "A requirement no code
implements" is a defect *only when the capability has shipped*. Several specs
deliberately run ahead of the build: `openspec/specs/archive-writing/` describes
`create()` and the `ArchiveWriter` surface, and the only code is an ABC in
`src/archivey/internal/base_reader.py` with no implementation, no tests, no docs —
that is Phase 9 and `VISION.md` says writing is not a 1.0 requirement. Phase 8
(seekable zstd / blocked gzip) is the same shape, with a live specs-only change
proposal in flight. **The authority is `openspec/project.md` §Capability map +
§Implementation order** — check a capability's phase there before flagging it, and
skip anything whose phase has not landed (also listed in §E). The target of this sweep
is the O-23 class: a spec clause describing behaviour of a **shipped** capability that
no code performs and no test asserts.

### C. Vocabulary / surface consistency (pre-freeze, cheap)

| Seed | Signal | Confidence |
|---|---|---|
| **`MemberStreams` vs `open_stream(seekable=…)`** | Same concept, two spellings (`code-self-documentation` C1 / `api-surface-suggestions`). Free only before `0.2.0`. | `CONFIRMED` split |
| **Extras naming vs capability** | `[recommended]` consolidation landed; codec install hints — confirm they no longer say "install `[7z]`" for a ZIP Deflate64 member (A1). | Re-verify post-consolidate |
| **CLI defaults vs library** | `must-explain` #23 — interactive CLI defaults diverge from library defaults. Product choice or accidental? Coordinate with archived `cli-product` decisions; do not re-litigate P4/`--json`. | Needs read |
| **Exception roots** | `ArchiveyUsageError` / `ConcurrentAccessError` outside `ArchiveyError` — deliberate (ADR 0012). In scope only if call sites put the *wrong* error outside/inside the tree. | Do not reopen ADR |
| **CLI import paths for public types** | `cli/extract_cmd.py` imports `ExtractionProgress` from the public `archivey` package; `cli/progress.py` and `cli/test_cmd.py` (and `tests/test_cli.py`) import the same type from `archivey.internal.extraction_types`. **Pre-answered: import from the public path.** The type *is* public (`archivey.__all__`); `internal/extraction_types.py` explains in its module docstring that the `internal/` home is an import-cycle workaround for public value types. So this is a spelling inconsistency, not an API gap or a freeze question. Worth a row because the review should ask whether the *pattern* is isolated. | `CONFIRMED`; trivial |

**A negative result worth recording:** the CLI does **not** reach into `internal/` for
anything it lacks a public route to — the only internal import is the one above, and
that type is already exported. The addendum's "CLI reaching into `internal/` is usually
an API gap" heuristic finds nothing here. Put that in "what is actually fine" rather
than re-deriving it; if the review finds a *second* pattern, that one is the real
signal.

### D. Simplicity (narrow structural, plus the caller-facing read)

| Seed | Signal | Confidence |
|---|---|---|
| **Pass-driver / member-list leftovers** | S2/S3 **paid** in debt-ledger (#184). Do not re-propose unification. Ask only: did post-#184 / #225 changes re-introduce a third copy or a backend-local bypass? | Regression check |
| **Reader close vs stream lifetime** | Just aligned with stdlib (`#225` / P7). Treat as settled; note only if a backend diverges. | Settled |
| **Module / helper sprawl** | Only flag a split or helper that *forces callers or backends into divergent paths*. Dead exports overlap archived api-coherence — coordinate, do not redo the `__all__` audit. | Low priority |
| **Dual pipelines that encode different contracts** | Progressive vs materialized listing should be one contract with two delivery shapes. If streaming TAR can miss a final corrupt header that RA catches, that is a consistency finding (honesty), not a request to merge the pipelines again. | Link to A |
| **Concept count for the common task** | The other four rows are *structural* simplicity; this one is what a caller actually feels. How many concepts must someone hold to open an archive and read a member — and how many caveats does each user-guide page need to stay true? The docs review just produced this evidence: `gotchas.md`, the per-page format-conditionals, and `must-explain.md` (29 behaviours not inferable from signatures). Count format-conditionals and Gotchas bullets **per page**; a page that needs many is a finding about the *library*, not the prose. This is also the review's only measurable before/after. | Evidence already exists |

### E. Explicitly out of the pay-down list (do not expand into)

- Salvage / best-effort read mode (`IDEAS.md`) — feature, not consistency.
- Native ZIP / multi-volume ZIP / native TAR walker (open-issues P2–P4) — product
  roadmap; this review may **label** them as "format-forced residual" so Gotchas
  stay honest, but does not design them.
- Decode-engine perf (Topic 6), adoption capstone (Topic 7).
- Debt-ledger KEEPs (DD5–DD12, T5/T6, N1 pyppmd, etc.) unless new evidence
  overturns the KEEP justification.
- Settled ADRs (0003 MemberStreams defaults, 0010 no pipe spool, 0012 usage
  errors outside the tree, 0013 name safety, 0014 integrity-from-reads).
- **Specs for capabilities whose phase has not landed** — `archive-writing`
  (Phase 9) and the Phase 8 seekable-zstd / blocked-gzip work. They are deliberately
  ahead of the code; they are not spec fiction and this review does not audit,
  implement, or propose deleting them. See the carve-out in §B.

## Suggested process

1. **Baseline — and check the environment before trusting a green suite.** Record
   pytest / ruff / pyrefly / ty / `openspec validate --all` at `a1cbf2a` (or whatever
   `main` is when the review starts). Note three-config only where a finding depends on
   optional libs.
   **The skip trap matters more here than in any other review.** Missing `unrar` / `7z`
   makes ~109 tests *skip quietly* while the suite still reports green (`CLAUDE.md`),
   and `tests/test_corpus_sweep.py` skips entire formats through the registry's
   availability guard. In a cross-format parity pass that turns into empty matrix cells
   indistinguishable from legitimate `N/A`. So: run `scripts/setup-dev-env.sh` (or
   confirm `unrar` and `7z` are on `PATH`), use the project venv — `archivey` is not
   importable otherwise — and **record `format_availability()` output as part of the
   baseline**. If a format is unavailable, its column is *unmeasured*, not `N/A`.
2. **Build the observable matrix — by running code, not by reading it.** Rows =
   caller-visible operations and fields (`open_archive` / `open_stream` / `extract`,
   listing, `open`/`read`/`stream_members`, passwords, digests, costs, errors, close
   lifetime). Columns = backends (ZIP, TAR, compressed TAR, 7z, RAR, ISO, single-file,
   directory) × source shapes (Path, seekable stream, non-seekable) where it matters.
   Start from `review/archive/2026-07-19-api-coherence/parity.md` and the formats.md
   matrices — update, do not redraw from blank.
   At 25 corpus format keys × a dozen operations × three source shapes, a hand-read
   matrix is expensive and stale on arrival. **Generate it**: write a probe script over
   the declarative corpus and commit it under `repro/`. The vehicles already exist —
   `tests/sample_archives.py` (`CORPUS`, `FORMAT_KEYS`), `tests/test_corpus_sweep.py`
   (parametrized corpus × format driver), `tests/test_reader_contract.py` (access-mode
   and `_SUPPORTS_RANDOM_ACCESS` gates), `tests/test_stream_inputs.py` /
   `test_short_read_sources.py` (source shapes).
   **Every cell says how it was established**: *observed* (the probe ran it), *read*
   (traced in source, not executed), or *unmeasured* (format unavailable — see step 1).
   `N/A` is only ever `N/A` **with a reason**; a bare `N/A` is an unfinished cell.
   **Definition of done: every cell is filled or `N/A`-with-reason.** No silent gaps.
   **Fill the *expected* column first** — this is where the §Known seeds counterweight
   lands. Once the rows exist but before the probe runs, write what each row *should*
   do from `VISION.md` + `openspec/specs/` alone, without looking at the code or the
   seeds. Then generate the observed column and diff. Cells where expectation and
   behaviour disagree are the review's primary leads; that ordering is what keeps the
   seeds from deciding in advance where to look.
3. **Checkpoint — show the matrix before deepening.** Stop here and hand the maintainer
   the drawn matrix plus a one-page read of where the divergences cluster. This pass's
   scope ("every observable × every backend") is wide enough that a mid-point re-aim is
   cheap and a wrong aim discovered at delivery is not. Continue on the maintainer's
   steer, or after a short wait if none comes.
4. **Apply the O-21 / O-26 method.** For each surprising cell: find the
   implementing line; check the branch where the happy path does not hold; check
   the spec before calling it a docs bug. Classify each divergence:
   - **Accident** — delete / align (candidate fix before `0.2.0`)
   - **Format law / stdlib / upstream** — must be **data** or a Gotchas bullet;
     confirm it is queryable and documented
   - **Explicit decision** — cite ADR / QUESTIONS answer; leave alone
   - **Spec fiction** — a **shipped** capability's spec clause that no code performs:
     drop the clause or implement it, never leave it aspirational. Specs for unlanded
     phases are not this (§B carve-out, §E)
   **"Format law" needs evidence, not intuition.** It is the bucket that closes a
   finding with no work, so it needs a citation: a format-spec section, observed
   behaviour of stdlib / `py7zr` / `rarfile` / `unrar` (the dev-group oracles named in
   `openspec/project.md`), or an `archivey-dev` reference (`AGENTS.md` §Reference
   repository). "I could not see how to avoid it" is not format law — that is an
   accident with an unknown fix, and it should be filed as one.
5. **Simplicity pass (short).** Only after the matrix: where do accidents share a
   root cause that one change deletes? Resist new abstractions. Include the §D
   concept-count read — the caller-facing half of "simplicity" is not visible in the
   structural rows.
6. **Rank for `QUESTIONS.md`.** Severity × confidence, then **freeze-cost** — meaning
   the cost *after* the tag, never a reason to accept a quirk today (see Hard
   constraints). Record the **fix vehicle** for each item, because it decides what is
   realistically payable before the tag: a cross-format contract move needs an OpenSpec
   change (`CONTRIBUTING.md`, addendum §3), a plain defect needs a red–green bugfix PR,
   and some findings are docs-only. Separate "fix before tag" from "accept and
   document" from "park as product work".

## Hard constraints

- **VISION tie-breakers and `review/README.md` conventions apply.**
- **Pause and ask** on spec/design discrepancies — do not silently pick a winner
  (`CONTRIBUTING.md`).
- **Do not weaken safety or honest-cost claims** to gain uniformity.
- **Runnable repros** for behavioural findings where practical; name the
  dependency config.
- **Coordinate with Topic 8 / in-flight docs.** Content inaccuracies go to
  `review/docs/observations.md` (or Topic 8); this review owns *library*
  behaviour and spec honesty. If unsure, file in both with a pointer.
- **Behaviour churn is free until the `0.2.0` tag — propose what you would choose on a
  blank page.** Nothing is on real PyPI; only `0.2.0.dev0` reached TestPyPI, a scratch
  index nobody depends on. So "this would be a breaking change" is **not** a reason to
  prefer documenting a quirk over deleting it. Freeze-cost enters the ranking only as
  an argument for fixing something *before* the tag, never for accepting it now.
  (The docs review's brief had to correct exactly this framing — an unstated
  compatibility cost had quietly biased it toward tidying pages in place. The same trap
  here would bias this review toward Gotchas bullets, which is the opposite of what
  §Values asks for.)
- **No library changes** until the maintainer picks pay items — no drive-by refactors,
  no fixes, however small and however `CONFIRMED`.
  **Test-only artifacts are explicitly allowed and wanted**, because they are evidence
  rather than fixes: the `repro/` probe scripts that generate the matrix (step 2), and
  assertions that **pin behaviour as it is today** so the same drift cannot silently
  return. Pinning current behaviour is not endorsing it — an assertion on a divergence
  the review classifies as an accident is the red half of the eventual red–green fix,
  and one on a divergence classified as format law is the guardrail that keeps it
  honest. Anything that *changes* behaviour waits for the pay list.

## Deliverables

| File | Contents |
|---|---|
| `SUMMARY.md` | Headline, top findings table (severity / where / **status** / proposed disposition), "what is actually fine" — including seeds that turned out to be non-issues and the negative results in §B/§C |
| Theme files | Suggested: `parity-matrix.md`, `silent-exceptions.md`, `vocabulary.md`; add `simplicity.md` only if structural items earn it |
| `QUESTIONS.md` | Maintainer decisions: each accident → pay / accept / park, **with fix vehicle** (spec change / bugfix PR / docs-only); vocabulary C1; the O-23 `warnings.warn` question; any spec fiction in a landed capability |
| `repro/` | The probe script(s) that generated the matrix, plus minimal repros for `CONFIRMED` behavioural findings. Not optional — the matrix is only as trustworthy as the thing that produced it |
| Guardrail assertions | For each divergence classified **format law**, an assertion in the conformance sweep so it cannot silently change; for each **accident**, a failing (or xfail) test that is the red half of the eventual fix. Test-only, behaviour unchanged — see Hard constraints. The docs review's own conclusion was that the guardrail phase is what decides whether a review has to be done twice |

## Provenance / do not resurface

Already closed or settled — cite and move on:

- api-coherence P1 duplicate `is_current` / SUPERSEDED; surface demotions; digest typing (#153–#160)
- debt-ledger S2/S3 pass-driver unification (#184); remaining KEEPs
- `#225`: solid-warning spec drop, directory `format=` reject, close-on-reader-close, seekable (non-Path) size/CRC probes, solid password laziness
- O-23 diagnostics-describe-archive rule
- ADR 0012 / 0010 / 0003 / 0014

If the review rediscovers one of these, the finding is either "regression since
settle" or "docs still wrong" — not a new design question.

**`openspec/changes/` at `a1cbf2a` is not all in flight.** Four directories sit
outside `archive/`, and three of them are *landed*:

| Change | State |
|---|---|
| `close-member-streams-on-reader-close` | **Done** (28/28 tasks) — `#225`; awaiting archive |
| `reject-format-override-on-directory` | **Done** (12/12) — `#225`; awaiting archive |
| `spec-drop-unimplemented-solid-warning` | **Done** (10/10) — `#225`; awaiting archive |
| `seekable-gzip-and-block-writing` | **Live** (0/24) — specs-only by design, carries its own `brief.md`, Phase 8 |

Read the first three as shipped behaviour, not pending work. Do not design against or
around the fourth: it is the Phase 8 forward spec the §B carve-out excludes, and it has
an owner.
