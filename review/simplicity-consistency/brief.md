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
already carries a **light recon** (§Known seeds); the review deepens and
verifies those, then expands.

**Analysis-first.** Propose and get decisions before changing code. When a finding
is `CONFIRMED` with a runnable repro, flag it as a red–green candidate — do not
fix it in this review.

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
| Light structural simplicity only where it deletes a failure class | Re-litigating debt-ledger KEEPs or settled ADRs |
| Ranking what to fix before `0.2.0` vs accept as format law | Topic 7 outside-in adoption capstone |
| Public-surface vocabulary leftovers that freeze at release | Feature work (salvage mode, native ZIP, multi-volume ZIP) |

## Known seeds (light recon — deepen, do not re-derive from zero)

These came from the docs cleanup observations, `#225`, `open-issues.md`,
`code-self-documentation.md` / `api-surface-suggestions.md`, and a short read of
backends + `core.py` at `a1cbf2a`. Each is a **starting lead**, confidence as tagged.

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
| **Spec promises without code** | Solid re-decode *warning* was specified and never implemented (O-23). Sweep live specs for "may warn" / "SHOULD emit" / scenario outcomes that no test asserts. | Method proven |
| **Silent argument discard** | Directory `format=` was the only explicit assertion overruled without error (`#225` / P8). Analogues: ignored config knobs, CLI flags that no-op on some formats, selector fields backends skip. | Method proven |
| **Diagnostics boundary** | New rule: diagnostics describe the **archive**, not the caller's usage (O-23). `STREAM_REWIND_REDECOMPRESSES` sits awkwardly on the usage side — flag only, do not churn unless the review finds a cleaner cut. P9 (`warnings.warn` on solid random open) is parked — decide or keep parked. | Decided rule; one awkward code |
| **Error translation consistency** | S1 boundary exists; spot-check whether newer paths (RAR unrar map, 7z pipeline, single-file, extract coordinator) still hand-roll translate/stamp or leak raw `ValueError` / `RuntimeError` / `NotImplementedError` across the public boundary. | Light grep shows ZIP still has careful tuples — verify others |

### C. Vocabulary / surface consistency (pre-freeze, cheap)

| Seed | Signal | Confidence |
|---|---|---|
| **`MemberStreams` vs `open_stream(seekable=…)`** | Same concept, two spellings (`code-self-documentation` C1 / `api-surface-suggestions`). Free only before `0.2.0`. | `CONFIRMED` split |
| **Extras naming vs capability** | `[recommended]` consolidation landed; codec install hints — confirm they no longer say "install `[7z]`" for a ZIP Deflate64 member (A1). | Re-verify post-consolidate |
| **CLI defaults vs library** | `must-explain` #23 — interactive CLI defaults diverge from library defaults. Product choice or accidental? Coordinate with archived `cli-product` decisions; do not re-litigate P4/`--json`. | Needs read |
| **Exception roots** | `ArchiveyUsageError` / `ConcurrentAccessError` outside `ArchiveyError` — deliberate (ADR 0012). In scope only if call sites put the *wrong* error outside/inside the tree. | Do not reopen ADR |

### D. Simplicity (structural — narrow)

| Seed | Signal | Confidence |
|---|---|---|
| **Pass-driver / member-list leftovers** | S2/S3 **paid** in debt-ledger (#184). Do not re-propose unification. Ask only: did post-#184 / #225 changes re-introduce a third copy or a backend-local bypass? | Regression check |
| **Reader close vs stream lifetime** | Just aligned with stdlib (`#225` / P7). Treat as settled; note only if a backend diverges. | Settled |
| **Module / helper sprawl** | Only flag a split or helper that *forces callers or backends into divergent paths*. Dead exports overlap archived api-coherence — coordinate, do not redo the `__all__` audit. | Low priority |
| **Dual pipelines that encode different contracts** | Progressive vs materialized listing should be one contract with two delivery shapes. If streaming TAR can miss a final corrupt header that RA catches, that is a consistency finding (honesty), not a request to merge the pipelines again. | Link to A |

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

## Suggested process

1. **Baseline.** Record pytest / ruff / pyrefly / ty / `openspec validate --all`
   at `a1cbf2a` (or whatever `main` is when the review starts). Note three-config
   only where a finding depends on optional libs.
2. **Build the observable matrix.** Rows = caller-visible operations and fields
   (`open_archive` / `open_stream` / `extract`, listing, `open`/`read`/
   `stream_members`, passwords, digests, costs, errors, close lifetime).
   Columns = backends (ZIP, TAR, compressed TAR, 7z, RAR, ISO, single-file,
   directory) × source shapes (Path, seekable stream, non-seekable) where it
   matters. Cell = behaviour or `N/A` with reason. Start from
   `review/archive/2026-07-19-api-coherence/parity.md` and the formats.md
   matrices — update, do not redraw from blank.
3. **Apply the O-21 / O-26 method.** For each surprising cell: find the
   implementing line; check the branch where the happy path does not hold; check
   the spec before calling it a docs bug. Classify each divergence:
   - **Accident** — delete / align (candidate fix before `0.2.0`)
   - **Format law / stdlib / upstream** — must be **data** or a Gotchas bullet;
     confirm it is queryable and documented
   - **Explicit decision** — cite ADR / QUESTIONS answer; leave alone
   - **Spec fiction** — drop the clause or implement; never leave aspirational
4. **Simplicity pass (short).** Only after the matrix: where do accidents share a
   root cause that one change deletes? Resist new abstractions.
5. **Rank for `QUESTIONS.md`.** Severity × confidence; freeze-cost (public
   behaviour / public names) as a third sort key. Separate "fix before tag" from
   "accept and document" from "park as product work".

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
- **No drive-by refactors** in the findings tree — analysis only until the
  maintainer picks pay items.

## Deliverables

| File | Contents |
|---|---|
| `SUMMARY.md` | Headline, top findings table (severity / where / proposed disposition), "what is actually fine" |
| Theme files | Suggested: `parity-matrix.md`, `silent-exceptions.md`, `vocabulary.md`; add `simplicity.md` only if structural items earn it |
| `QUESTIONS.md` | Maintainer decisions: each accident → pay / accept / park; vocabulary C1; P9 solid `warnings.warn`; any spec fiction |
| Optional `repro/` | Minimal scripts for `CONFIRMED` behavioural findings |

## Provenance / do not resurface

Already closed or settled — cite and move on:

- api-coherence P1 duplicate `is_current` / SUPERSEDED; surface demotions; digest typing (#153–#160)
- debt-ledger S2/S3 pass-driver unification (#184); remaining KEEPs
- `#225`: solid-warning spec drop, directory `format=` reject, close-on-reader-close, seekable (non-Path) size/CRC probes, solid password laziness
- O-23 diagnostics-describe-archive rule
- ADR 0012 / 0010 / 0003 / 0014

If the review rediscovers one of these, the finding is either "regression since
settle" or "docs still wrong" — not a new design question.
