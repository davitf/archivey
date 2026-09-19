# Archivey Review Addendum

> **This is a repo-specific focus doc layered on top of the generic code-review skill.**
> Use the skill’s process, severity labels, and Python/quality guides as the base;
> use **this file** for what archivey uniquely cares about.
>
> **Review order:** archivey PRs use **code first, then context** (§8) — not the generic
> “read the design narrative before the diff” order.
>
> Do not merge these rules into the upstream-derived guides — keep the delta visible.

**Authoritative sources (read these when a finding touches them):**

| Source | Role |
|--------|------|
| [`VISION.md`](../../../../VISION.md) | Product tie-breaker when trade-offs conflict |
| [`CONTRIBUTING.md`](../../../../CONTRIBUTING.md) | Coding, typing, exceptions, testing, three-config gate |
| [`openspec/specs/`](../../../../openspec/specs/) | Capability contracts — starting point for behavior, revisable when wrong (§3) |
| [`dev-docs/threat-model.md`](../../../../dev-docs/threat-model.md) | Trust boundaries + open security gaps |
| [`review/README.md`](../../../../review/README.md) | Deep-review conventions, ranking, deliverable shape |
| [`review/STATUS.md`](../../../../review/STATUS.md) | Live triage of in-flight review follow-ups |

---

## 0. Finding discipline (how to report)

Archivey reviews optimize for **maximum code quality with a human maintainer as the
filter** — not for an automated gate minimizing false positives. So **over-report on
existence, be rigorous on labeling.** Raise the concern; never suppress a real one
because you're unsure. The discipline is honest labeling, not silence.

Output is markdown prose, never a host-specific findings tool (`SKILL.md`) — the two-axis
+ reclassification model below is richer than those schemas. Posting to a PR → §10.

### Output shape — three blocks (required)

Exactly three top-level sections, in this order. The maintainer often has **not** read the
diff: design for that reader first, the implementor second. Do not lead with a findings
dump — dense detail belongs in block 2. [`assets/pr-review-template.md`](../assets/pr-review-template.md)
is the fill-in form.

**Brevity fence.** Short form applies only to how blocks 1 and 3 are *presented*. It must
not reduce review depth (full §8–§9 passes, same tracing and checklists), finding
discipline (over-report on existence; severity × confidence), **block 2** specificity, or
real pause-and-ask items in block 3 — when unsure whether something needs a human call,
include it and label confidence. If block 1 is short because the analysis was thin, that
is a failed review, not compliance with this shape.

#### 1. Maintainer briefing (read this first)

For the maintainer skimming without the code open. Half a screen unless the change is huge.

- **What this change is** — 2–4 sentences: intent, main files/areas touched, behaviour
  delta, in plain language. Assume no familiarity with the PR or the OpenSpec change name.
  **Once per PR, not once per reviewer.** Write it only if no review on this PR carries
  one yet. The maintainer has already read it otherwise, and that is true whether the
  previous review was yours or the other reviewer's — a second reviewer's first look is
  not a re-review under §10, but the description is just as redundant. Any later review
  opens with the status table over whatever IDs already exist (§10) instead. The one
  exception is a rework that made the earlier description wrong: then give one line on
  what changed, not a fresh description.

  (Fix-diff scope, §10, stays per *reviewer* — a second reviewer has not read the tree
  and reads `main...HEAD`. It is only the description that is per PR.)
- **Snapshot** — size (approx. lines / small|medium|large), gates (CI status, §10), and
  **Verdict**: ✅ Approve / ✅ Approve conditional on the listed fixes / 💬 Comment /
  🔄 Request Changes — meanings below, and “only nits left” is not an approval.
- **Main points** — ranked one-liners, 🔴/🟡 only: severity + gist. No `file:line` essays.
- **What's fine** (optional, 1–3 bullets) — load-bearing things that looked correct, so the
  briefing isn't only negatives.

Zero findings worth action? Say so here and keep blocks 2–3 minimal (`None.`).

#### 2. Implementor handoff (goes on the PR)

For whoever fixes or replies, **on the PR** — §10 splits it there: located findings become
inline threads, the rest goes in the body. Write it to survive that split and to be read
months later by someone who has only the thread in front of them: a one-line context header
(PR / branch / scope), the full findings, then the Verdict line again so block 2 does not
depend on block 1. No "as above" / "see briefing" — those break the moment the blocks are
separated.

Standing alone is about surviving §10, not about being carried elsewhere: the PR is the
only destination, and nothing here is written for anyone to take away (the rule is under
block 3 below).

**Evidence, not prose.** Each finding carries severity, confidence, location (`file:line`),
what's wrong, why it matters, fix direction, and a trigger / repro note where possible
(`CONFIRMED` + trigger ⇒ red–green candidate, §4). Don't restate the diff, narrate your
process, or pad with transitions. Terse is not thin: cutting evidence to look brief
violates the brevity fence, cutting prose does not.

Rank by severity, then confidence. Include 🟢 nits and 💡 suggestions here, not in the
briefing.

#### 3. Maintainer decisions (your attention)

**Only** items that need a human call — this block is the maintainer UI; block 2 is worker
mail for whoever addresses the PR. Use the canonical decision packet in
[`dev-docs/pair-workflow.md`](../../../../dev-docs/pair-workflow.md) §Decision packet —
same six fields, same cold-start test. Do not invent a shorter parallel list. Routine
"please add a test for X" fixes belong in block 2. Nothing to decide → `None.` Do not
invent filler questions, and do not drop a real decision gap to keep the section empty.

Posting for a split implementor/maintainer workflow: blocks 1–2 go on the PR; if you also
chat with the maintainer, send **block 3 packets only** unless they ask for the full
handoff.

**The posted review is the whole handoff — do not also write a prompt for the implementing
agent.** No “paste this into a fresh agent” brief, no prompt file, no second copy of the
findings in chat or in a scratch document. The implementing agent runs
[`address-review-findings`](../../address-review-findings/SKILL.md) and reads the PR
threads itself, so the copy is redundant on the day it is written and wrong a round later,
when a thread has been replied to and the copy has not. Block 2 stands alone so that it
survives being **split across PR threads** — not so the maintainer has something to carry
to a worker. A fix direction too long for a thread still belongs in the thread.

**A decision the maintainer already settled is no longer a block 3 item.** Move it into
block 2 where the implementor works, marked as theirs and not yours:

> **Maintainer decision (davitf): fix, do not defer.** Nits on this PR are to be closed,
> not carried.

Re-raising a settled call sends the implementor back to the maintainer for an answer that
exists; leaving it unmarked lets it read as reviewer preference, which gets argued with or
skipped. Name the decider and link the comment or packet where there is one. A
recommendation the maintainer has **not** ruled on stays yours, however confident — do not
promote your own preference to "decided" because nobody objected.

**Do not hold the review waiting on a decision that has already been made.** Once every
block 3 packet is settled, the review goes on the PR. The counterpart rule for the
responding agent is in
[`address-review-findings`](../../address-review-findings/SKILL.md) §6; both exist because
a settled decision that lives only in a chat transcript is lost — a fresh container has no
memory of earlier sessions (`CLAUDE.md`).

Reviewing an OpenSpec proposal (§9) uses the same three blocks: "what this change is"
summarizes the proposal's intent, and block 2 is the handoff for whoever revises the
proposal or implements it later.

### Verdicts — what each one commits to

The Snapshot verdict is a claim about whether the PR is ready to merge **as it stands**,
so use these meanings and no others:

- **✅ Approve** — nothing left to change. Every finding is `DISPROVEN`, already fixed in a
  later commit, or a 💡 / 📚 / 🎉 annotation that carries no action.
- **✅ Approve, conditional on the listed fixes** — the only remaining findings are 🟢 nits
  (or a 🟡 that small) whose fix is *obvious*, and you would not need to see the result. Say
  which IDs the approval is conditioned on, in the verdict line itself. This is the one
  verdict that approves with work outstanding; the conditioned findings are still posted in
  full (block 2, inline threads, IDs) — approving is not shorthand for dropping them.
- **💬 Comment** — findings the implementor should act on, but nothing the maintainer must
  decide and nothing you need to re-review.
- **🔄 Request Changes** — a 🔴 stands, or a fix needs a look once it lands.

**"Only nits left" is not a reason to merge.** In this repo a 🟢 nit is *small*, not
*optional*: it gets fixed before merge, on this PR. "Leave it for a follow-up" is not an
available disposition — nobody comes back for it, and the next agent has no memory of this
round (`CLAUDE.md`). So never write "good to merge, only nits remain": either the nits are
fixed, or the verdict is the conditional approval above, which keeps them on the
implementor's list. Only the maintainer waives a nit, explicitly; when they do, record it
per the settled-decision rule in block 3.

The 💡 / 📚 / 🎉 tiers are the genuinely optional ones — they propose or teach, they do not
ask for a change, and they never hold a merge.

### Round budget — from round 3, nits do not hold the PR

**From the third round on, if only 🟢 nits remain open, the verdict is
"✅ Approve, conditional on the listed fixes" and you stop reviewing.** Do not open a
fourth round to confirm wording changes you already described. Whether it then merges is
the maintainer's call, as always — this bounds re-reading, not merging.

This is not a relaxation of the nit rule — the conditioned findings are still posted in
full and still fixed. It is a bound on *re-reading*. In the two weeks to 2026-09-19, every
🔴 in the repo was raised in round 1 or 2; rounds 3 to 6 produced almost only 🟢 wording
findings, and #326 reached six rounds. Late rounds also manufacture their own work: seven
findings in that window existed only because an earlier fix on the same PR created them
(a fix that deleted the rationale the finding asked for, a fix that broke `__del__`, a
correction to wording a previous fix introduced).

A round 3+ that finds a 🔴 or a 🟡 is a normal round — say so and keep going. The budget
binds only the case where the remainder is nits.

### Two axes: severity ≠ confidence

Rate every finding on both axes so the maintainer reads **severity × confidence** and
decides:

- **Severity** — impact *if the finding is real*: 🔴 `[blocking]` / 🟡 `[important]` /
  🟢 `[nit]` (plus 💡 / 📚 / 🎉 non-blocking). Mapping for this repo: §7.
- **Confidence** — `CONFIRMED` (you traced the actual failing path: definitions, callers,
  guards) / `PLAUSIBLE` (real risk, not fully traced or no repro built) / `DISPROVEN` (you
  traced it and the code is correct — **route it, don't delete it**).

Low confidence lowers the *confidence tag*, never the decision to report: a
🔴 `PLAUSIBLE` finding is still reported.

### Verification routes findings; it never silently culls them

After the code + context passes, re-trace each candidate (§8 "trace, don't
pattern-match"). Tracing changes the tag and may *reclassify* — it does not delete real
concerns. When a finding comes back `DISPROVEN`, ask *"why did I, reading carefully, think
this was broken?"*:

- A careful reader could reasonably have misread it → the code isn't self-documenting.
  Re-file as 🟡 **clarity / doc-debt** (§8's documentation-debt rule) — a comment, clearer
  name, or an `assert` that encodes the invariant.
- Tracing revealed a genuine but non-obvious invariant → 💡 / 📚: suggest the comment or
  assertion that would have made it obvious.
- A careless misread ordinary attention would have avoided → drop it; a 🟢 nit is fair if
  it still cost real review effort.

Only a careless self-misread is ever dropped. Everything else becomes a (possibly smaller)
finding.

### Failure scenario: requested, not gating

Name a concrete trigger where you can — input / archive / state → wrong result, crash, or
contract violation. `CONFIRMED` + repro ⇒ flag it as a **red–green regression-test
candidate** (CONTRIBUTING wants red–green for bug fixes, §4). Can't build one → the finding
still stands, tagged `PLAUSIBLE` / needs-repro. A missing repro lowers confidence, never
existence.

### Keep findings disciplined (inside block 2)

Over-reporting fails only when it is *unlabeled*. Hold the noise down by discipline, not
suppression:

- **Dedupe by root cause** — one finding per cause; cite one site, list the rest.
- **Rank** by severity, then confidence within a tier.
- Briefing (block 1) stays thin; detail lives in the handoff (block 2); decisions (block 3)
  stay only what needs you.

---

## 1. What you are reviewing

Archivey is a **sync-first, zero-dep-core Python library** for reading, streaming, and
safely extracting archives (ZIP / TAR / ISO / directory / single-file codecs; native
7z/RAR). There is no web UI, SQL/ORM, or HTTP product surface. The CLI is a **wedge
and second consumer** of the library API — useful evidence of API gaps, not the main
act (`VISION.md`).

### Load-bearing claims (rank findings against these)

From `VISION.md` / `review/README.md` — a finding that undercuts a marketing claim
outranks a same-severity nit that does not:

1. **One uniform interface** with honest cost / capability signals (no silent
   per-format guesses).
2. **Safe by default** — zip-slip, symlink escape, and decompression bombs require
   explicit opt-out; safety is a contract, not a flag.
3. **Memory-safe parsing of hostile input** — pure-Python parsers preferred so crafted
   archives cannot *corrupt* native parser memory; failures must be honest errors.
4. **Damaged input is first-class** — recoverable members + an honest error beat a
   bare exception at open (salvage mode is backlog; don’t invent it in a PR review).
5. **≤ ~1.3× stdlib** on common ZIP/TAR open/list/read/extract paths (up to ~2× when
   safety/correctness justifies it). Track **bytes decompressed and seeks**, not only
   wall time — silent solid-block re-decode fails the budget even if a tiny fixture hides it.

### Non-goals (don’t demand these in reviews)

- Async public API in v1
- Compatibility shims for `zipfile` / `py7zr` / `rarfile` APIs
- Quirk-driven architecture that lets third-party reader quirks leak into core contracts
- In-place archive modification / encryption-write for 7z/RAR

---

## 2. “No surprises” — the standing design rule

Behavior differences between formats must be **data** (`None`, explicit enums,
documented sentinels) — never silent guesses (`openspec/project.md`, `VISION.md`).

When reviewing cross-backend changes, ask:

- [ ] Does every backend give this field the **same meaning**?
- [ ] If a backend cannot provide it, is emptiness / `None` documented and asserted?
- [ ] Would a caller branching on the field trip on a format-specific surprise?
- [ ] Should the declarative corpus / conformance sweep grow an assertion?

Parity hot spots from past reviews: `member.hashes`, `ListingCost` / `AccessCost`,
`MemberStreams` / `StreamCapability`, timestamps/mode/links/`MemberType` (incl. `ANTI`),
duplicate-name / `is_current` semantics.

---

## 3. Coding & contract checks (`CONTRIBUTING.md`)

These are **review blockers** when violated — not style nits.

### Zero-dep core & extras

- [ ] Core / native 7z read / RAR metadata import **no** third-party packages at runtime
- [ ] New deps land only as optional extras and match `packaging-and-extras`
- [ ] Optional imports are lazy at the right boundary (don’t pull extras into core import)

### Types

- [ ] Public API and anything feeding it is typed; `py.typed` story preserved
- [ ] Both **Pyrefly and ty** stay clean (not mypy/pyright)
- [ ] `# type: ignore` / checker suppressions are **specific**, rare, and **reasoned**
  inline — unjustified suppressions are blocking

### Exception translation

- [ ] Archive problems surface as `ArchiveyError` subclasses via the reader translator
- [ ] Known third-party errors map to the right type (`CorruptionError`,
  `TruncatedError`, `EncryptionError`, …)
- [ ] **No catch-all** `except Exception` that converts unknowns — return `None` from
  the translator and let unrecognized exceptions propagate
- [ ] `OSError` / `KeyboardInterrupt` / `MemoryError` propagate unless a spec says
  otherwise (e.g. safe-extraction `OnError.CONTINUE`)
- [ ] `ArchiveyUsageError` stays **outside** the archive-error tree (caller misuse)

### Zero tech debt (and clean-as-you-go)

The project aim is **debt-free** — not “clean enough,” but *no deliberately carried
debt* (`review/backlog.md`). Clean-as-you-go is how day-to-day PRs enforce that:

- [ ] Touched code is left in the shape it *should* have (rename / move / small
  refactor in the same change when the design requires it)
- [ ] Don’t land a “we’ll clean this later” shortcut without an **explicit, justified
  decision** (PR note, `QUESTIONS.md`, `IDEAS.md`, or `review/backlog.md`) — unspoken
  deferrals are debt
- [ ] Duplication, drift, and TODOs introduced or left adjacent to the change are
  either **paid now** or recorded as keep-with-reason — not ignored
- [ ] **Pause and ask** on real design discrepancies — do not silently pick a winner
  (`CONTRIBUTING.md`, `CLAUDE.md`, `review/README.md`)
- [ ] A **pre-existing bug in the mechanism this PR is already editing**, where the fix is
  proportionate, is in scope and is a normal finding — do not soften it to "pre-existing,
  not this PR's" or route it to the backlog. Being in a touched *file* is not the test;
  the mechanism under change is (`CONTRIBUTING.md` §Coding standards).
  A **sweep** across files the PR does not touch is the follow-up
  (`CONTRIBUTING.md` §Coding standards). This one is settled: the maintainer has ruled
  "fix it in this PR" on #342, #344 and #349, and "not in this PR" only where the ask was
  a cross-file sweep (#339, #353)

### Specs & OpenSpec changes

Specs are **guidelines for intended behavior**, not holy writ. Reviewers and authors
should treat them as the best current description of the contract — and revise them
when reality or a better design wins.

- [ ] **Not every change needs a spec.** Bug fixes, refactors, tests, tooling, docs
  polish, and internal cleanups usually do not. Prefer a spec/`openspec/changes/`
  delta when the **public or cross-format behavior contract** moves (or when an
  in-flight change proposal already owns the work).
- [ ] When a change *does* move a contract, update the relevant
  `openspec/specs/` (or propose via `openspec/changes/`) and matching user/decision
  docs in the **same** change — don’t leave prose lying.
- [ ] **If following a spec yields a worse outcome**, don’t contort the code to satisfy
  the letter of the doc. Surface it: prefer changing the spec (or opening a change
  proposal / maintainer question) so the written contract matches the better design.
- [ ] Spec ↔ doc ↔ code conflicts still use **pause-and-ask** — guessing bakes the
  wrong decision in. The goal is an explicit revision, not silent divergence.
- [ ] Open threat-model gaps (`O*`) are not “fixed” by marketing language alone

### Comments

- [ ] Explain *why* (format quirks, hostile-input edges), not narrate *what*
- [ ] Resulting code is self-explanatory (`CONTRIBUTING.md`); OpenSpec / PR prose is not
  the only explanation — future editors see the tree, not the diff
- [ ] Links to specs / decisions / explorations / OpenSpec changes are fine for complex
  decisions — but an inline summary should usually carry the *why*
- [ ] Match surrounding comment density
- [ ] **No history in `src/` comments** — "previously", "the old implementation", "this
  change", a PR number, an OpenSpec change name, a work-batch name (`Parcel B`, `Wave 1`),
  or an argument corrected against something the reader cannot see
  (`CONTRIBUTING.md` §Coding standards)
- [ ] **No comment left pointing at what the diff removed** — a call site that no longer
  exists, or a case the change made unreachable. Read the comments around every hunk,
  not just the changed lines
- [ ] **No claim stronger than the code guarantees** — a stated bound, ratio or
  invariant is something a reader will rely on; check it against the code or ask for the
  measurement

These three are the largest finding category in this repo by a wide margin. They are
deliberately a reviewer's job rather than a checker's: the wording is what makes them
wrong, and a grep for the phrasing would miss the ones that matter and fire on the ones
that do not.

---

## 4. Testing expectations

Review whether the change *has* the right tests; don't re-run the suite (§10).

- [ ] Prefer **behavior** assertions on the public API; unit-test stream/parser/codec
  internals when they are shared foundations
- [ ] Corrupt, truncated, encrypted, wrong-password, empty members, weird names,
  non-seekable sources are in scope — especially when touching readers/translators
- [ ] Use the **declarative corpus** / conformance sweep where format×shape coverage
  matters (`testing-contract`)
- [ ] Bug fixes: **red–green** — failing repro first, then fix
- [ ] **New guard / property / inventory tests: which mutation did they fail against?**
  A test added to defend an invariant is not done until the invariant has been broken and
  the test watched to fail, and the PR should say which mutation was applied
  (`CONTRIBUTING.md` §Testing standards). "Passes vacuously", "cannot fail for its stated
  reason", and "the fixture never reaches this path" are the recurring shapes here
- [ ] Say which dependency config a finding needs: `[all]`, `[all-lowest]`,
  `[core-only]` (`CONTRIBUTING.md`)
- [ ] Format before commit (`ruff`); don’t bike-shed formatting in review

Past review lesson: “no test in the suite catches this” is often a **strategy** gap
(property/fuzz/fault-injection), not only a missing example — flag thin coverage
honestly (`review/backlog.md` Topic 4).

---

## 5. Domain checklist (PR-sized)

Use alongside the skill’s generic checklist. Severity: 🔴 blocking / 🟡 important /
🟢 nit — same labels as the skill.

### Safety & hostile input

- [ ] Extract paths: traversal, absolute/UNC, null bytes, symlink/hardlink escape,
  never-write-through-symlink (`threat-model`, `safe-extraction`)
- [ ] Bomb / resource limits: output caps, ratios, entry counts, listing limits where
  applicable
- [ ] Every **policy** bound is **reachable from `ArchiveyConfig`** (`ListingLimits` /
  `ExtractionLimits`, raisable to `UNLIMITED`). A new `_MAX_…` constant inside a parser
  or reader is a finding — it is invisible from the API and turns a real archive into an
  error the caller cannot accept — **unless the bound is structural rather than policy**,
  which CONTRIBUTING allows with a reason at the constant and a spec row
  (`CONTRIBUTING.md` §Coding standards). Check for that reason before filing
- [ ] Parser bounds: huge length/count fields from headers cannot OOM the process
- [ ] Subprocess (`unrar`, fixture `7z`, …): list args, no `shell=True` interpolation
- [ ] Passwords / key material absent from logs, `repr`, and exception messages

### Streaming, cost model, performance

- [ ] Hot paths stream; avoid slurp-then-parse unless justified
- [ ] Solid / multi-member access does not **silently** re-decompress the same block
- [ ] Cost signals (`ListingCost` / `AccessCost`) stay honest if behavior changes
- [ ] Prefer stored digests (`member.hashes`) over decompress-to-hash when the format
  provides them
- [ ] Perf claims cite bytes/seeks or existing `benchmarks/` — not vibes

### API & layering

- [ ] Public vs `internal/` boundary respected (CLI reaching into `internal/` is a
  smell — often an API gap; see `review/api-coherence/`)
- [ ] New exports are intentional freeze surface; don’t grow `__all__` casually
- [ ] Format backends stay behind the uniform reader contracts
- [ ] Sync-first: no accidental async public API

### Specs & docs (quick)

- [ ] Spec update only when the behavior contract moves — see §3
- [ ] Don’t reject a better design solely because an old spec forbids it; propose
  revising the spec instead
- [ ] Don’t demand a new OpenSpec change for pure refactors / bugfixes with no
  contract delta

---

## 6. Deep reviews (`review/`) — when the skill expands into a brief

For commissioned deep reviews (not ordinary PR review), inherit
[`review/README.md`](../../../../review/README.md):

1. **Baseline first** — record green gates (pytest / skips, pyrefly, ty, ruff) and
   which dependency config. Overrides §10's no-re-run default — no CI run to inherit.
2. **VISION ranking** — order findings by load-bearing claims (§1).
3. **Deliverable shape** — `SUMMARY.md` (headline + severity table + status), theme
   files, `QUESTIONS.md` for maintainer decisions, and a **“what is actually fine”**
   section.
4. **Evidence** — `file:line`, concrete triggering input/state, runnable repro when
   practical.
5. **Pause and ask** — spec/design conflicts go to `QUESTIONS.md`, not silent fixes
   (including “the spec is wrong; here’s the better contract”).
6. **Don’t re-litigate settled ground** — check archive tables + `STATUS.md` for
   already-closed findings before spending budget.
7. **Archive lifecycle** — only move a review to `review/archive/` when every
   actionable item is fixed or consciously deferred (`STATUS.md` / `backlog.md`).

Review themes to know. **`review/STATUS.md` is the live index — read it rather than this
table**, which records lenses, not state. A theme listed as archived means findings in
that area are *re-reviews*: check the archive tables first so you do not re-litigate
settled ground (`review/backlog.md` carries the deferred topics and their reasons).

| Review | Lens | State |
|--------|------|-------|
| `docs/` | Documentation IA, then content accuracy/gaps (Topic 8) | **In flight** — see `STATUS.md` |
| `api-coherence/` | Uniform interface, surface size, CLI-as-consumer gaps | Archived |
| `performance/` | ≤1.3× budget, gate efficacy, solid/listing hotspots | Archived |
| `debt-ledger/` | Freeze-cost debt; corpus matrix (`corpus-matrix.md`) | Archived |
| `stream-layering/` | Wrapper correctness + collapse | Archived |
| `cli-product/` | CLI UX / grammar / exit codes (product, not correctness) | Archived |
| `simplicity-consistency/` | Topic 9 — duplicated concepts, inconsistent surfaces | Archived 2026-08-15 |
| Security round | Hostile input, crypto, RAR, stream decoder | Archived |

---

## 7. Severity mapping for this repo

| Label | Archivey examples |
|-------|-------------------|
| 🔴 `[blocking]` | Path escape on default extract; catch-all exception translation; core grows a hard dep; unjustified type suppressions; silent solid O(n²) on a common API; deliberate new debt with no recorded decision; public contract change left undocumented *and* undiscussed |
| 🟡 `[important]` | Dishonest cost signal; format parity hole without docs; missing red-green test for a bugfix; threat-model gap touched but unaddressed; CLI forced to import `internal/`; code contorted to match a questionable spec without raising a revision; non-obvious logic that only makes sense after reading OpenSpec/`design.md`/long PR prose (pass-1 doc debt, §8) |
| 🟢 `[nit]` | Naming, comment polish, non-user-facing refactor suggestions — *small*, not *optional*: still fixed on this PR (§0 Verdicts) |
| 💡 / 📚 / 🎉 | Alternatives, teaching notes, praise — non-blocking |

When unsure whether something is 🔴 vs 🟡: **does it undercut a VISION claim or the
error/safety contract?** If yes → 🔴.

---

## 8. Suggested review order (PR) — code first, then context

This replaces the generic “absorb the design narrative before the diff” order. For
archivey PRs, use two passes with different jobs. Context is still **required** — it
comes second, not never.

> **Scope:** this order is for **code / PR reviews**. Reviewing an OpenSpec proposal,
> delta spec, or `design.md` instead? There is no resulting code tree to read cold —
> use **§9** (values-first), not this order.

### Before either pass (logistics only — ≤1 minute)

The four-item list lives in `SKILL.md` → Logistics (scope, CI status, artifact names,
this section). Do **not** read the OpenSpec change, design notes, or long PR rationale
yet — names only.

### Pass 1 — code alone

Read the changed code (diff + nearby context) **cold**. Ask:

- [ ] Does the **resulting** code make sense **self-contained** — logic, edge cases,
  API shape, safety/streaming/cost without needing external docs?
- [ ] Are **non-obvious** choices explained **in the code** (or an adjacent module
  docstring / comment) — format quirks, hostile-input edges, why this branch /
  sentinel / exception path exists?
- [ ] Would a future editor who only has the tree (not the PR or OpenSpec change)
  understand *why*, not just *what*?
- [ ] Tests: behavior coverage, red–green for fixes (§4); domain checklist rows that
  are visible from the change (§5)

Per-area list: `SKILL.md` → Pass 1. Skim §1–§5 as a **mental checklist**, not by
loading linked designs.

**Documentation debt rule:** if a pass-1 concern only dissolves after reading
external prose (OpenSpec `design.md`, long PR body, `dev-docs/decisions/`, …), that is
usually **🟡 `[important]` documentation debt in the code** — not proof you should
have absorbed the design first. A comment that **summarizes *why* inline** and
optionally points at a spec / decision / exploration is fine; a bare “see design.md”
with no local reason is not. Specs and design notes explain *why we chose this
approach*; they are a poor substitute for *why this local path exists*.

### Pass 2 — whole context (do not skip)

Now open the narrative and contracts:

1. PR description + linked issue / full OpenSpec change (proposal, delta specs,
   `design.md`) / `review/` brief or finding.
2. Applicable rows in this addendum (§1 VISION ranking, §3 contracts, §5 domain)
   and authoritative sources at the top of this file when a finding touches them. For a
   contract-moving change, run the **values & contracts consistency check (§9)** — the
   same checklist proposals get, applied to the resulting behavior.
3. Spec ↔ code ↔ docs: match, intentional revision, or **pause-and-ask** (§3) —
   including “self-contained and clear, but disagrees with the capability scenario
   / invents undecided behavior / breaks format parity.”
4. Run a command only to reproduce or test a specific claim — not to re-run the
   gates — and then say exactly what you ran (§10).
5. Write feedback in the **three-block output shape (§0)** — briefing, implementor
   handoff, maintainer decisions — with skill severity labels; put pause-and-ask items
   in block 3, not silent resolutions.

| Pass | Job | Pass if… |
|------|-----|----------|
| **1. Code alone** | Correctness, clarity, local docs for non-obvious choices | Resulting code is self-explanatory; surprises are explained near the code |
| **2. Context** | Fit to OpenSpec / VISION / threat model / this addendum | Behavior matches (or intentionally revises) contracts; no silent discrepancies |

**Do not treat a solid pass 1 as license to skip pass 2.** A locally clear change can
still undercut a VISION claim, disagree with a scenario, or land unjustified debt.

---

## 9. Reviewing OpenSpec proposals & design docs (not code)

§8's **code-first** ordering is for **actual code / PR reviews**. When the artifact under
review is an OpenSpec **proposal**, delta spec, or `design.md` — not a diff — there is no
"resulting tree" to read cold. Review it against the project's **values and contracts**
instead. (This same check is pass-2 step 2 for code reviews — see §8; for a proposal it
is the *whole* review.)

Finding discipline (§0) applies unchanged: severity × confidence, over-report and label,
pause-and-ask on conflicts rather than silently reconciling.

### Values & contracts consistency check (VISION / CONTRIBUTING)

Run every proposal — and every contract-moving code change — past these:

- [ ] **Uniform interface / no surprises (§2):** one honest interface preserved? Every
  per-format behavior difference expressed as **data** (`None`, enums, documented
  sentinels), never a silent guess?
- [ ] **Safe by default (§1.2):** zip-slip / symlink escape / bombs still require explicit
  opt-out; the design doesn't quietly relax a safety contract.
- [ ] **Memory-safe hostile parsing (§1.3):** pure-Python parse boundaries preserved;
  crafted input yields honest errors, not native-memory corruption.
- [ ] **Damaged input is first-class (§1.4):** recoverable-members + honest-error posture
  preserved; salvage isn't invented where the backlog hasn't committed to it.
- [ ] **Cost honesty & perf budget (§1.5):** cost signals stay truthful; ≤ ~1.3× budget
  acknowledged in bytes/seeks (not vibes) where the design touches hot paths.
- [ ] **Contracts (§3):** zero-dep core, exception-translation model, sync-first, typing
  story, extras layering — none silently broken by the *design*.
- [ ] **Non-goals (§1):** not smuggling in an async public API, `zipfile`/`py7zr`/`rarfile`
  compat shims, quirk-driven architecture, or in-place 7z/RAR modification.
- [ ] **Threat-model gaps (`O*`):** open gaps the proposal touches are addressed in
  substance, not closed by marketing language.

### Proposal-shape checks

- [ ] **Scope right-sized (§3):** does this actually need a spec/change, or is it a
  bugfix/refactor that moves no contract?
- [ ] **Scenarios are falsifiable:** WHEN/THEN reads as testable behavior, not aspiration
  — a reviewer could write the conformance assertion.
- [ ] **Cross-format parity considered:** the parity hot spots in §2 are addressed where
  the change spans backends.
- [ ] **Error / edge / hostile paths specified**, not just the happy path (§4, §5).
- [ ] **Rationale present:** `design.md` records alternatives considered and the *why*,
  per the library schema — not just the *what* (stub OK for trivial deltas).
- [ ] **Docs move together:** if the contract moves, the matching `openspec/specs/` and
  user/decision docs move in the same change (§3).
- [ ] **Pause-and-ask** on conflicts with existing specs / docs / VISION — surface, don't
  silently reconcile (§3).
- [ ] **Names of public types are settled here, not after implementation.** If a proposal
  introduces a public class, protocol or config field, the name is a reviewable item at
  *this* stage — raise it now. Once the type is implemented, a rename is a spec and
  archive sweep rather than a one-line edit, so after implementation a rename needs a
  reason beyond taste and is the maintainer's call. (`FullCountStream` cost two rounds on
  #333 for exactly this reason, and the maintainer's note there was "we should have
  caught this while reviewing the spec".)

### Decision gaps & unknown unknowns

The checks above verify what the proposal *says*; this step hunts what it **doesn't**.
Go looking, don't wait for gaps to surface during implementation.

- [ ] **Implementor decision gaps** — read it as if you must implement it tomorrow. What
  would force you to *guess*? Under-specified error behavior, ambiguous field meaning,
  unhandled format/edge combinations, boundary/empty/overflow values, ordering,
  defaults, concurrency. List each as an explicit question the proposal should **decide
  before coding**, not during.
- [ ] **Unknown unknowns** — what is the proposal not thinking about? Format quirks not
  yet considered, interactions with existing capabilities, cross-format parity fallout
  (§2 hot spots), perf/cost surprises, security edges, dependency/version assumptions.
  Name what we *don't yet know* that could change the design, and how to shrink the
  unknown (spike, oracle comparison against `archivey-dev`/`py7zr`/`rarfile`, corpus
  probe, or a maintainer decision).
- [ ] **Assumptions taken on faith** — for each load-bearing assumption, is it verified
  or assumed? Flag the untested ones and the cheapest way to test them.

These are findings too (§0): a decision gap that could send implementation down the wrong
path is 🟡+ and belongs in **block 3 (Maintainer decisions)** — pause-and-ask, never a
silent assumption baked into the review. Detail for whoever revises the proposal goes in
block 2.

Rank the same way (§0/§7): a proposal that undercuts a load-bearing VISION claim (§1) is
🔴; a decision gap or thin scenario is 🟡; wording nits are 🟢. Emit the same
three-block output shape (§0).

---

## 10. Posting the review to a pull request

The usual workflow here is that **a second agent posts this review to the PR, and the
implementing agent then works through it** (`.claude/skills/address-review-findings/`).
That handoff is the reason for the rules below: a review that reads well in a terminal but
cannot be dispositioned finding-by-finding costs the next round more than it saved.

### Stable IDs, one thread per finding

- **Give every block-2 finding a stable ID and keep it across re-reviews** — a re-review
  of `F3` says `F3`, not `2`. The responder's status table and the maintainer's memory
  both key on them; renumbering between rounds silently breaks both.
- **Prefix the ID with your own initial** (`C1`, `C2`, … from Cursor; `K1`, `K2`, … from
  Claude Code) rather than a bare `F`. Two reviewers work the same PR here, and a bare
  `F` collides: #353 carried two different `F16`s, from two reviewers, at the same time,
  and the responder had to disambiguate them by hand. Keep counting up across your own
  rounds on that PR — `K7` follows `K6` even in a later round.
- **Post each block-2 finding that has a `file:line` as an inline review comment** anchored
  there, not buried in one long top-level wall. Inline findings can be replied to and
  resolved individually, which is what makes the state of a round visible later.
- **Post blocks 1 and 3 as the review body** (or a top-level comment): the maintainer
  briefing and the decisions are about the change as a whole and have no line to anchor to.
- Findings without a location — process, missing coverage, contract drift across documents
  — stay in the top-level body, still with IDs.

Where the host cannot post inline comments, one top-level comment is acceptable, but the
IDs are not optional.

### Whole-file sweeps: one `SWEPT` marker per file

A **sweep batch** is a cold whole-file reading pass rather than a diff review — the `S*`
batches posted to [#315](https://github.com/davitf/archivey/pull/315). Findings post exactly
as above, one inline thread each. What a sweep posts *in addition* is a per-file record that
the file was read at all, **whether or not it found anything**:

- **One top-level comment on #315 per file**, posted when you finish reading that file and
  before you start the next one. Not inline: a whole-file read has no line to anchor to.
- **The marker is always its own comment, including for a file that produced findings.**
  Never put the `SWEPT` line in a review body, a finding, or a reply. A file with findings
  therefore gets its findings *and* a marker comment, which is the point: the marker says
  the file was read end to end, and the findings say what was in it. Keeping markers in one
  comment type is what makes the whole set fetchable in one call — the counting command in
  [`open-work-inventory.md`](../../../../dev-docs/open-work-inventory.md) reads the issue
  comments and nothing else.
- Its **first line** is the marker, in exactly this shape:

  ```
  **SWEPT** `src/archivey/internal/backends/zip_reader.py` — pass=S1 date=2026-09-17 lines=1612 findings=3 ids=S1-F1,S1-F2,S1-F3 reviewer=cursor head=94468bd
  ```

  `**SWEPT**`, the repo-relative path in backticks, an em dash, then the fields as
  `key=value` in that order, space separated, **no spaces inside a value**.

  | Field | What |
  |---|---|
  | `pass=` | The batch id — `S1`, `S3`, … The 2026-09-08 pass is `S0`. Split a batch as `S3a` / `S3b` |
  | `date=` | The ISO date you read the file |
  | `lines=` | The file's line count at `head` |
  | `findings=` | How many block-2 findings you raised against this file |
  | `ids=` | Their IDs, comma separated; `-` when `findings=0` |
  | `reviewer=` | `claude-code` or `cursor` |
  | `head=` | The commit you read the file at |

- Under the marker, three to five lines of prose: what you read, what you checked that came
  back clean, and anything you deliberately left to another batch. **A clean file's comment
  is the short one and the valuable one** — it is the only thing that distinguishes a file
  that was read and found sound from a file nobody opened.
- **A sweep finding's ID is `<pass>-<your initial><n>`** — `S16-K1`, `S17-C1` — which carries
  both prefixes this file already requires: the batch, so two batches on #315 cannot collide,
  and the reviewer's initial, so two reviewers in one batch cannot either. **This applies from
  the next batch and renames nothing.** An ID that is already anchored in a posted thread is
  never renumbered (§"Stable IDs"), so a batch that used another form keeps it and says so in
  its markers. `ids=` therefore carries whatever IDs the findings actually have, and nothing
  reading a marker may assume they share the `pass=` value.
- **One marker per file per pass**, and a batch posts each file's marker once. Re-sweeping
  a file in a later batch posts a **new** marker rather than editing the old one: the newest
  wins and the older stays as the record of what was true then. `sweep_coverage.py` counts a
  path once however many markers it carries, so a slip cannot inflate the figure — it warns
  instead when one path carries two markers from the same pass.

`lines=` is recorded as read, not looked up later, because coverage decays: the first
backfill showed seven of nine files from the 2026-09-08 pass more than 10% away from the shape
that pass read, all in the one package whose findings had since been fixed. Draining a sweep's
threads rewrites the code the sweep read, so a marker that could only say "swept" would
overstate the subsystem where the follow-up was most thorough. Record the drift; when to act
on it is the maintainer's to schedule, and as of 2026-09-19 the answer is after the first
pass over the whole codebase, not during it.

**A marker for a read you did not perform carries `backfilled=<date>` as a final field**, and
its prose says in as many words that nobody re-read the file. That form exists for one event
— the sixteen files swept on 2026-09-08 and 2026-09-17, before the convention, backfilled on
2026-09-19 at the maintainer's decision from the batch scope tables and the paths the threads
landed on. A pass that predates stable finding IDs writes `ids=untagged`, and one whose
reviewing host is not recorded writes `reviewer=unknown`. Do not reach for any of the three
when recording your own read.

**Why this exists.** Findings are evidence of a read; the absence of findings is not. The
coverage figure on [`open-work-inventory.md`](../../../../dev-docs/open-work-inventory.md)
was overstated by twelve points in two consecutive snapshots because threads were counted as
coverage, and `backends/rar_parser.py` — the largest file in the repository and its most
exposed hostile-input surface — was believed swept when no agent had ever read it. Two sweep
threads also reached opposite conclusions about the same two files from the same evidence.
Markers make "was this file swept?" answerable by looking rather than by inference.

The marker line carries the reviewer and the head, so on this comment type it **replaces**
the §"A short marker at the top" opener rather than sitting under it. Attribution and footer
rules are unchanged.

Coverage is counted from these markers, never from thread counts —
[`dev-docs/open-work-inventory.md`](../../../../dev-docs/open-work-inventory.md) §How sweep
coverage is counted, and `scripts/sweep_coverage.py`, which does the counting.

### Re-reviews state what happened to the last round

A second pass on the same PR opens with a **status table over the previous IDs** — fixed /
still open / superseded — before any new findings. Say which HEAD you reviewed. If a rework
made an earlier review obsolete, say so explicitly rather than leaving two contradictory
reviews for the responder to reconcile.

**Review the fix-diff, not the PR again.** From round 2 on, the scope is
`git diff <the-SHA-you-last-reviewed>..HEAD` plus the still-open threads — not
`main...HEAD`. You already read the rest; re-reading it is the largest avoidable cost in
this loop, and it manufactures findings of its own, because a fix made for round 1 is the
thing round 2 then reports (an over-deleted rationale, a `__del__` broken by the previous
fix, wording introduced by the previous wording fix).

Two exceptions, both narrow: the head was rebased or force-pushed, so the previous SHA is
no longer an ancestor; or a fix changed a contract, in which case re-read the callers of
what moved, not the whole diff. Say which scope you used in the Snapshot line.

### Do not re-run the gates — or re-measure what a previous round recorded

The implementer and CI already ran `check.sh` / `test.sh`. A review does **not**
re-run ruff, pyrefly, ty, or the test suite. Glance at CI in logistics (§8) only
enough to know whether failures are in-scope. The Snapshot "gates if known" line
is that status — not a claim you re-ran them.

Run a command only when the review itself needs a result CI cannot give:
reproducing a suspected bug, checking a runtime claim, confirming a "does not
reproduce." When you do, say exactly what you ran. A "does not reproduce" with
no command is still a copied claim.

**The same rule applies to your own earlier rounds.** Rebuilding a fixture, re-timing a
bomb, re-running a mutant, or re-deriving an offset that a previous round already
established is the single largest measured waste in this loop — on #342 the same
arithmetic was re-derived from scratch in three consecutive rounds (twice wrongly), and
#349 and #353 rebuilt 70,000-file fixtures and re-timed both bombs in rounds 2, 3 and 4.

So **close every review body with a `Measured this round` list** — one line per command,
with its result:

```
### Measured this round
- `python scripts/make_7z_bomb.py --members 70000` → 2.1 s, 4.4 MB header
- `pytest tests/test_sevenzip_limits.py -k bcj2` → 12 passed, RESTART BLOCKS SEEN: [0]
```

The next round inherits that list and re-runs only what the new HEAD invalidates. When
you do re-run something, say what changed to make it necessary. An empty list is a
perfectly good answer and should be written as `None.`

**CI not posted** (unpushed, or still running): the Snapshot line says
`gates: CI pending`. Don't infer a result, don't stand in for CI — ask the author
to push.

**Exception — commissioned `review/` briefs** (§6): baseline first still holds.
No CI run to inherit, and the skip count is itself evidence.

### You cannot post a GitHub approval — expected, not news

Agents here post through the maintainer's own account (the same fact §Attribution is
about), and GitHub refuses `event: APPROVE` on your own pull request:

```
422 Unprocessable Entity — Can not approve your own pull request
```

This is the normal, permanent state of this repo's review loop, not a failure to report.
So:

- **Submit the review with `event: COMMENT`** (`pull_request_review_write`, method
  `submit_pending`). `REQUEST_CHANGES` is rejected on your own PR for the same reason —
  `COMMENT` is the only event that goes through.
- **Carry the verdict in the text**, where the §0 Verdict line already puts it. The
  briefing's `✅ Approve` / `✅ Approve conditional on K4, C5` / `🔄 Request Changes` is the
  review's actual conclusion; the green check in GitHub's UI is not available to say it.
- **Do not narrate the limitation** to the maintainer as a discovery each round, and do not
  retry `APPROVE` to see if it works this time. If it is worth a line at all, it is one
  clause in the handoff ("verdict in text; GitHub blocks self-approval"), not a paragraph.
- A human approval, where the repo's checks require one, is still the maintainer's to give.
  Nothing you post substitutes for it.

Hosts that post as their own bot identity (`cursor[bot]`, …) are not their own PR author
and are not covered by this — they may post the real event.

### Attribution — make it obvious an agent wrote this

Agents usually post through the maintainer's own GitHub account, so **every posted comment
must be identifiable as agent-authored**. Without that, a review is indistinguishable from
the maintainer's own comment, and the next agent cannot tell which feedback is the human's
— a distinction that changes how the feedback is weighted (see the responder skill's "Who
actually said this").

Two mechanisms satisfy this; use whichever your environment gives you:

- **A distinct bot account.** If your host posts as its own identity (`cursor[bot]`,
  `qodo-code-review[bot]`, …), attribution is already unambiguous and no footer is needed.
- **An attribution footer**, when posting through a human account. End the comment with a
  rule and one italic line naming the tool that wrote it. In Claude Code sessions that is:

  ```
  ---
  _Generated by [Claude Code](https://claude.ai/code)_
  ```

  Other hosts use their own equivalent — the requirement is the *identifiability*, not that
  specific string. Do not sign a comment with a tool that did not write it.

**Claude Code: do not write that footer yourself — the tool appends it.** Verified on #326
against review bodies, inline review comments, and thread replies: each came back carrying
exactly one server-added footer when the posted text had none. A footer you add as well is
deduplicated on inline comments and replies, but **not** on a review body, which then shows
it twice ([review 5178024776](https://github.com/davitf/archivey/pull/326#pullrequestreview-5178024776)).
Leave it off and let the tool add it.

This is host-specific. **Cursor and any other host whose posting path does not append a
footer must still add its own** — the requirement is identifiability, and a comment posted
through the maintainer's account with no marker fails it. If you do not know whether your
host appends one, post one comment without it and read the stored body back before assuming.

### A short marker at the top, not only a footer

A footer is only visible once the reader reaches the end. Open **every** posted comment —
review body, inline finding, reply — with one short line naming the agent, the skill, and
the HEAD it reviewed:

```
**Claude Code** · `code-review-skill` · review of `3060ac51`
```

On a PR whose threads mix maintainer questions, `cursor[bot]` dispositions, and a reviewer
posting through the maintainer's account, this is what makes a thread scannable — the
author avatar says `davitf` for two of those three. Keep it to one line; the detail belongs
in the finding.

### Name the responder skill in the review body

End block 3 with a one-line pointer to the responder skill:

```
Addressing these: `.claude/skills/address-review-findings/SKILL.md`.
```

The findings are not always picked up by an agent that was asked to address them. A
session subscribed to PR activity reacts to the review as an *event*, from its own
generic posture, and never invokes a skill nobody named — so the pointer is the only
thing in the PR that says which process applies. Claude Code watchers also read
`.claude/skills/steward/SKILL.md` before acting; the line in the body is what covers
everyone else.

### Do not fix while reviewing

`/code-review-skill` reports; it does not edit (SKILL.md, repo default). Leaving the fix to
the implementing agent is what keeps the review a second opinion rather than a self-graded
one.

---

## 11. Out of scope for *this* addendum

Generic SOLID, Python footguns, and review etiquette stay in the skill’s existing
docs (`architecture-review-guide.md`, `python.md`, `code-review-best-practices.md`,
…). This file only carries **archivey product/contract standards** distilled from
VISION, CONTRIBUTING, and the `review/` program.
