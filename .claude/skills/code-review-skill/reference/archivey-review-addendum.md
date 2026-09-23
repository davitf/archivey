# Archivey Review Addendum

> **This is a repo-specific focus doc layered on top of the generic code-review skill.**
> Use the skill’s process, severity labels, and Python/quality guides as the base;
> use **this file** for what archivey uniquely cares about.
>
> **Review order:** archivey PRs use **code first, then context** (§8) — not the generic
> “read the design narrative before the diff” order.
>
> Do not merge these rules into the upstream-derived guides — keep the delta visible.

**Read at the start of a review:**

| Source | Role |
|--------|------|
| [`CONTRIBUTING.md`](../../../../CONTRIBUTING.md) | **The coding and testing rules themselves** — typing, exceptions, comments, config bounds, the three-config gate. §3 and §4 below say which of them PRs here break; they do not restate them |

**Authoritative sources (open these when a finding touches them):**

| Source | Role |
|--------|------|
| [`VISION.md`](../../../../VISION.md) | Product tie-breaker when trade-offs conflict |
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

Three blocks, and **they do not all go in the same place.** Blocks 1 and 3 are the review
body; block 2 is the inline threads, one per finding (§10). The body is therefore a
summary with an index, and the detail sits on the line it concerns.

That split is a maintainer decision (davitf, 2026-09-21): *"I'm finding PR review comments
too long and hard to find the relevant info."* A body that also carries every finding in
full is the thing being fixed here — the detail is not deleted, it moves to the thread
where it can be replied to and resolved.

The maintainer often has **not** read the diff: design the body for that reader, and the
thread for the implementor. [`assets/pr-review-template.md`](../assets/pr-review-template.md)
is the fill-in form.

**Every posted comment opens with a header carrying the round and the verdict** — review
body, inline finding and reply alike. Exact shapes: §10 "Open every comment with a header".

**No tables in anything you post.** They wrap into unreadable columns on a phone, which is
where the maintainer reads them. Bullets instead, one per row. This is about *posted*
comments: the tables in this file and in the rest of the repo are unaffected.

**Brevity fence.** Short form applies only to how blocks 1 and 3 are *presented*. It must
not reduce review depth (the full §8 passes, and §9 where the change moves a contract;
same tracing and checklists), finding
discipline (over-report on existence; severity × confidence), the specificity of a finding
**in its thread**, or real pause-and-ask items in block 3 — when unsure whether something needs a human call,
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
  opens with the status bullets over whatever IDs already exist (§10) instead. The one
  exception is a rework that made the earlier description wrong: then give one line on
  what changed, not a fresh description.

  (Fix-diff scope, §10, stays per *reviewer* — a second reviewer has not read the tree
  and reads `main...HEAD`. It is only the description that is per PR.)
- **Findings** — the index, and the only list of findings in the body. One bullet each,
  ranked by severity then confidence, including 🟢 and 💡: ID, severity, confidence, the
  gist in one sentence, the location, and a link to the thread. Nothing else. The full
  finding is in the thread, and a body that repeats it is the length problem this shape
  exists to fix. **Post the inline comments first, then the body**, because a review
  submitted in one call creates both at once and the body would have no URLs to link to
  (`POST .../pulls/{n}/comments` returns each comment's `html_url`, and §10 records that
  a review body cannot be edited afterwards through the MCP tools). A finding whose thread
  URL you genuinely cannot get is listed with its `file:line` alone rather than held back.
- **Snapshot** — one line: size (approx. lines / small|medium|large), the scope you
  reviewed, and gates (CI status, §10). The verdict is already in the header, so the
  Snapshot does not restate it.
- **What's fine** (optional, 1–3 bullets) — load-bearing things that looked correct, so the
  briefing isn't only negatives. Put it inside the collapsed block (§10) rather than in the
  running text.

Zero findings worth action? Say so here and keep blocks 2–3 minimal (`None.`).

#### 2. Implementor handoff (goes on the PR)

For whoever fixes or replies. **This block is not text in the review body — it is the
inline threads**, one per finding, anchored on the line it concerns (§10). The body carries
only the index from block 1.

Write each thread to be read months later by someone who has only that thread in front of
them: the finding's own header (§10), then what is wrong, why it matters, the fix
direction, and the trigger. No "as above" / "see the briefing" / "see K3" — a thread that
points at another comment is unreadable the moment someone opens it from a notification.

**A finding with no location stays in the body**, under the index and in full, still with
its ID: process, missing coverage, contract drift across documents. Those are the exception
and they are usually few; when they are not, the body grows and that is correct.

Standing alone is about surviving that split, not about being carried elsewhere: the PR is
the only destination, and nothing here is written for anyone to take away (the rule is under
block 3 below).

**Evidence, not prose.** Each finding carries severity, confidence, location (`file:line`),
what's wrong, why it matters, fix direction, and a trigger / repro note where possible
(`CONFIRMED` + trigger ⇒ red–green candidate, §4). Don't restate the diff, narrate your
process, or pad with transitions. Terse is not thin: cutting evidence to look brief
violates the brevity fence, cutting prose does not.

Every finding gets a thread, 🟢 nits and 💡 suggestions included. Ranking happens in the
index; a thread is ranked by where it sits in the file.

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

The header's verdict is a claim about whether the PR is ready to merge **as it stands**,
so use these meanings and no others:

- **✅ Approve** — nothing left to change. Every finding is `DISPROVEN`, already fixed in a
  later commit, or a 💡 / 📚 / 🎉 annotation that carries no action.
- **✅ Approve, conditional on the listed fixes** — the only remaining findings are 🟢 nits
  (or a 🟡 that small) whose fix is *obvious*, and you would not need to see the result. Say
  which IDs the approval is conditioned on, in the heading itself. This is the one
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

**Under the automated loop the verdict is what stops the loop**, not a suggestion it
weighs — and unlike the budget above it, that is not a round-3 rule: a conditional
approval at round 1 ends the loop at round 1. The round's verdict file maps 🔄 Request
Changes to a closing comment that asks for another round, and the three "I do not need
to see the result" verdicts — a plain approval, the conditional approval above, and
💬 Comment — to one that asks for none, whatever rounds the cap has left
([`dev-docs/review-loop.md`](../../../../dev-docs/review-loop.md) §Stopping it). So the
verdict line is a decision about spending another review, and writing 🔄 to keep a round
in hand spends one on wording you have already described.

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
- Briefing (block 1) stays thin and carries the index; detail lives in each finding's own
  thread (block 2); decisions (block 3) stay only what needs you.

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

**`CONTRIBUTING.md` §Coding standards holds the rule text, and holds it once.** Read it at
the start of a review, not once per finding. What follows is the reviewer's half: which of
those rules PRs in this repo actually break, and how to label a break. Violating one is a
**review blocker**, not a style nit.

- [ ] **Zero-dep core.** Core, native 7z read and RAR metadata import nothing third-party
  at runtime; a new dependency lands as an optional extra matching `packaging-and-extras`;
  optional imports stay lazy at the boundary
- [ ] **Types.** Public API and what feeds it is typed, the `py.typed` story is preserved,
  Pyrefly *and* ty stay clean, and every suppression is specific and reasoned inline
- [ ] **Exception translation.** `ArchiveyError` subclasses via the per-reader translator;
  no catch-all; `OSError` / `KeyboardInterrupt` / `MemoryError` propagate;
  `ArchiveyUsageError` stays outside the archive-error tree
- [ ] **A changed exception type was checked against what catches it upstream** — here a
  type is control flow, and the PR should say what the grep found
- [ ] **Nothing drops or clamps silently** — an over-long read, a seek past a boundary, a
  consumed count
- [ ] **Every policy bound is reachable from `ArchiveyConfig`.** A new `_MAX_…` inside a
  parser or reader is a finding *unless* the constant carries a stated structural reason
  and a spec row, which CONTRIBUTING allows. Check for that reason before filing
- [ ] **Clean-as-you-go, with no unspoken deferral.** A "we'll clean this later" shortcut
  needs an explicit home (PR note, `IDEAS.md`, `review/backlog.md`); an unrecorded one is
  debt
- [ ] **Pause-and-ask** on a real design discrepancy — neither author nor reviewer
  silently picks a winner
- [ ] **Comments** explain *why*, carry no history, point at nothing the diff removed, and
  claim nothing stronger than the code guarantees

### The comment rules need a reviewer, not a checker

Stale and overclaiming comments are the largest finding category in this repo by a wide
margin, and they are deliberately not automated: the wording is what makes them wrong, so a
grep would miss the ones that matter and fire on the ones that do not. Read the comments
*around* every hunk, not only the changed lines.

### Pre-existing bugs — settled, do not re-escalate

A pre-existing bug in the **mechanism the PR is already editing**, where the fix is
proportionate, is in scope and is a normal finding. Do not soften it to "pre-existing, not
this PR's" or route it to the backlog. Being in a touched *file* is not the test. A
**sweep** across files the PR does not touch is the follow-up. The maintainer has ruled
"fix it in this PR" on #342, #344 and #349, and "not in this PR" only where the ask was a
cross-file sweep (#339, #353) — so this one does not need another decision packet.

### Specs

Specs are the best current description of the contract, not holy writ.

- [ ] Not every change needs one. Bugfixes, refactors, tests, tooling and docs polish
  usually do not; a spec delta is for a moving **public or cross-format contract**
- [ ] When a contract does move, the spec and the matching docs move in the **same** change
- [ ] A spec that yields a worse outcome gets **revised**, not worked around — surface it
  instead of contorting the code to satisfy the letter of the doc
- [ ] Open threat-model gaps (`O*`) are not closed by prose alone

---

## 4. Testing expectations

Rule text: `CONTRIBUTING.md` §Testing standards. Review whether the change *has* the right
tests; do not re-run the suite (§10).

- [ ] Behaviour assertions on the public API, plus unit tests for the shared foundations —
  stream primitives, format parsers, the codec layer
- [ ] Corrupt, truncated, encrypted, wrong-password, empty, weird-named and non-seekable
  cases, especially when the change touches readers or translators
- [ ] The declarative corpus / conformance sweep where format×shape coverage matters
  (`testing-contract`)
- [ ] A bug fix has a red–green repro
- [ ] **A new guard, property or inventory test names the mutation it failed against.**
  The PR must say which one was applied. "Passes vacuously", "cannot fail for its stated
  reason" and "the fixture never reaches this path" are the recurring shapes here
- [ ] A finding that depends on extras says which config it needs: `[all]`,
  `[all-lowest]`, `[core-only]`

"No test in the suite catches this" is usually a **strategy** gap — property, fuzz,
fault-injection — not one missing example. Flag thin coverage honestly
(`review/backlog.md` Topic 4).

---

## 5. Domain checklist (PR-sized)

The archive-specific traps, alongside the skill's generic checklist. Where one of these
constrains how code must be written, `CONTRIBUTING.md` carries the rule; this is what to go
looking for. Severity: 🔴 blocking / 🟡 important / 🟢 nit.

### Safety & hostile input

- [ ] Extract paths: traversal, absolute / UNC, null bytes, symlink and hardlink escape,
  never write through a symlink (`threat-model`, `safe-extraction`)
- [ ] Bomb and resource limits: output caps, ratios, entry counts, listing limits — and
  reachable from `ArchiveyConfig` (§3)
- [ ] Parser bounds: a huge length or count field from a header cannot OOM the process
- [ ] Subprocess arguments are a list, and passwords or key material are absent from logs,
  `repr` and exception messages (`CONTRIBUTING.md`)

### Streaming, cost model, performance

- [ ] Hot paths stream rather than slurp-then-parse, unless the PR justifies it
- [ ] Solid / multi-member access does not silently re-decompress a block, and the cost
  signals still match what the code does (`CONTRIBUTING.md`)
- [ ] Stored digests (`member.hashes`) preferred over decompress-to-hash where the format
  provides them
- [ ] Perf claims cite bytes and seeks, or an existing `benchmarks/` run

### API & layering

- [ ] Public vs `internal/` boundary respected, and new `__all__` entries are intentional
  (`CONTRIBUTING.md`). The CLI reaching into `internal/` usually means an API gap — see
  `review/api-coherence/`
- [ ] Format backends stay behind the uniform reader contracts
- [ ] Sync-first: no accidental async public API

### Specs & docs (quick)

- [ ] A spec delta only when the behaviour contract moves, and no new OpenSpec change
  demanded for a pure refactor or bugfix (§3)

### Red flags — worth a grep on any diff

An empty `except:` or a swallowed error · `shell=True` with interpolation · an ad-hoc path
join on an extract destination · a TODO in a production path with no home · commented-out
code · a magic number in a parser · a copy-pasted codec or backend block that should share
a helper · a hardcoded credential.

---

## 6. Deep reviews (`review/`) — when the skill expands into a brief

Commissioned deep reviews inherit [`review/README.md`](../../../../review/README.md) and a
different deliverable shape — baseline gates recorded rather than inherited, `SUMMARY.md`
plus theme files, `QUESTIONS.md` for maintainer decisions, and the archive lifecycle.
**[`deep-reviews.md`](deep-reviews.md)** carries it, including the theme table and why
`review/STATUS.md` is the live index. Ordinary PR review does not need that file.

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
   contract-moving change, run the **values & contracts consistency check** — the same
   checklist proposals get, applied to the resulting behavior. It is the first section of
   [`reviewing-proposals.md`](reviewing-proposals.md), and the only one a code PR needs
   (§9).
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

A proposal has no resulting tree to read cold, so it gets a **values-first** order rather
than §8's: VISION and CONTRIBUTING consistency, then proposal shape, then decision gaps
and unknown unknowns. **[`reviewing-proposals.md`](reviewing-proposals.md)** carries that
order and its checklists.

It has two readers, and they open different amounts of it:

- **Reviewing a proposal, a delta spec or a `design.md`** — the whole file, in its order.
- **Reviewing a contract-moving code PR** — only the **values & contracts consistency
  check**, which is §8's pass-2 step 2. The proposal-shape and decision-gap sections do not
  apply; the resulting code is what you have. The naming rule lives in the proposal-shape
  list, so a PR that adds a public type or config field wants that list too.

An ordinary code PR that moves no contract does not open it at all.

---

## 10. Posting the review to a pull request

The usual workflow here is that **a second agent posts this review to the PR, and the
implementing agent then works through it** (`.claude/skills/address-review-findings/`).
That handoff is the reason for the rules below: a review that reads well in a terminal but
cannot be dispositioned finding-by-finding costs the next round more than it saved.

### Stable IDs, one thread per finding

- **Give every block-2 finding a stable ID and keep it across re-reviews** — a re-review
  of `F3` says `F3`, not `2`. The responder's status list and the maintainer's memory
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
  The body's finding list is the **index only** — one bullet per finding, linking to its
  thread (§0). Do not also paste the findings into it.
- Findings without a location — process, missing coverage, contract drift across documents
  — stay in the top-level body, still with IDs, and there they are written in full.

Where the host cannot post inline comments, one top-level comment is acceptable, but the
IDs are not optional.

### Whole-file sweeps: one `SWEPT` marker per file

A **sweep batch** is a cold whole-file reading pass rather than a diff review — the `S*`
batches posted to [#315](https://github.com/davitf/archivey/pull/315). Findings post exactly
as above, one inline thread each. What a sweep posts *in addition* is a per-file record that
the file was read at all, **whether or not it found anything**:

- **One top-level comment on #315 per file**, posted when you finish reading that file and
  before you start the next one. Not inline: a whole-file read has no line to anchor to.
- **Writing about the hub elsewhere?** Never reproduce a closing phrase next to its number
  in a commit message or a pull request body — it closes the hub, silently, and quoting one
  counts. The rule lives in [`AGENTS.md`](../../../../AGENTS.md) §Review workflow; it bit
  twice on 2026-09-21. Inline comments like the ones above are not parsed, so findings and
  markers are unaffected.
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
the §"Open every comment with a header" opener rather than sitting under it — a sweep
marker comment has no round and no verdict, and the marker line already carries the
reviewer and the head. Attribution and footer
rules are unchanged.

Coverage is counted from these markers, never from thread counts —
[`dev-docs/open-work-inventory.md`](../../../../dev-docs/open-work-inventory.md) §How sweep
coverage is counted, and `scripts/sweep_coverage.py`, which does the counting.

### Re-reviews state what happened to the last round

A second pass on the same PR opens, under the header, with **one bullet per previous ID** —
fixed / still open / superseded — before any new findings. Not a table (§0): a four-column
table of round-1 claims is the single worst thing to read on a phone, and the claim column
is a copy of last round's body, which is one scroll away.

One line each, and the line says what is true now, not what was claimed then:

```
**Round 1 — K1…K5.** All fixed at `f65b6d05`; I re-derived each rather than taking the replies.

- **K1** 🔴 fixed in `5afdbb5` — the parser claim is gone from both files.
- **K2** 🟡 fixed in `5afdbb5` — now "two surfaces, not one", carried into `AGENTS.md`.
- **K7** 🟡 still open — the label does not exist yet.
```

Say which HEAD you reviewed. If a rework made an earlier review obsolete, say so explicitly
rather than leaving two contradictory reviews for the responder to reconcile. Where a
status needs more than a line, it needs a reply on that finding's own thread instead — that
is where whoever fixed it is looking.

**Review the fix-diff, not the PR again.** From round 2 on, the scope is
`git diff <the-SHA-you-last-reviewed>..HEAD` plus the still-open threads — not
`main...HEAD`. You already read the rest; re-reading it is the largest avoidable cost in
this loop, and it manufactures findings of its own, because a fix made for round 1 is the
thing round 2 then reports (an over-deleted rationale, a `__del__` broken by the previous
fix, wording introduced by the previous wording fix).

**Read your own previous review bodies first, not just the open threads.** In the loop
each round is a fresh session with no memory of the last one, so the earlier review *is*
the handoff, and it carries what a thread does not: block 1's briefing, block 3's
decisions, and the reasoning behind a finding rather than its one-line statement. The
threads are also the wrong place to look for a complete picture, because a resolved or
collapsed one drops out of the default view while the review body stays. Read every
review you posted on this PR, including rounds whose findings are all closed. This does
not reopen the cost rule above: the bodies are a few kilobytes, and you are fetching the
PR's comments for the status bullets anyway.

**Then check what each fix reaches.** The narrow scope is safe for a fix that stays inside
its own lines and unsafe for one that does not, and the difference is not visible from the
fix-diff. For each fix, ask whether it moved a signature, a return-or-raise contract, a
default, an invariant, or the lifetime of something a caller holds. Where it did, read the
callers of what moved before judging the fix. This is a check with an answer rather than
something to notice in passing: say in the Snapshot line which fixes you traced outward
and what came back, or that none of them moved a contract. A fix that is correct in
isolation and wrong for one caller is the failure this scope would otherwise let through,
and it is the one a round-1 reviewer is least likely to catch, because the fix is its own
suggestion coming back.

Two exceptions to the scope itself, both narrow: the head was rebased or force-pushed, so
the previous SHA is no longer an ancestor; or the trace above sent you to callers outside
the fix-diff, which you read rather than the whole diff. Say which scope you used in the
Snapshot line.

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
with its result. **It is written for the next round, not for the maintainer, so collapse
it**, along with the other next-round material: "What's fine", and the outward-trace note.
GitHub renders `<details>` as a one-line disclosure triangle, so the data survives at no
cost to the reader who does not want it:

```
<details><summary>Measured this round · what's fine · outward trace</summary>

- `python scripts/make_7z_bomb.py --members 70000` → 2.1 s, 4.4 MB header
- `pytest tests/test_sevenzip_limits.py -k bcj2` → 12 passed, RESTART BLOCKS SEEN: [0]

</details>
```

The blank lines inside the block are required — GitHub does not render markdown flush
against the tags. Nothing a decision depends on goes in here: findings, the verdict and
block 3 stay in the open text.

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
- **Carry the verdict in the text**, where the §10 header already puts it. The
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

**Claude Code: whether to write the footer yourself depends on the posting path, so check
before you post a batch.** On #326, against review bodies, inline review comments and thread
replies, each came back carrying exactly one server-added footer when the posted text had
none; a footer added as well is deduplicated on inline comments and replies but **not** on a
review body, which then shows it twice
([review 5178024776](https://github.com/davitf/archivey/pull/326#pullrequestreview-5178024776)).

**On #315 through the GitHub MCP tools, nothing is appended.** Five sweep batches verified it
independently on 2026-09-20 by reading their own stored bodies back, and two of them lost the
footer on their first review body before noticing. Write it explicitly there and confirm
exactly one per comment. A review body cannot be edited through the MCP tools, only patched
with a direct API call, so a body posted without one is expensive to fix — which is the
argument for running the one-comment check first, as the paragraph below already says.

This is host-specific. **Cursor and any other host whose posting path does not append a
footer must still add its own** — the requirement is identifiability, and a comment posted
through the maintainer's account with no marker fails it. If you do not know whether your
host appends one, post one comment without it and read the stored body back before assuming.

### Open every comment with a header

A footer is only visible once the reader reaches the end, and a comment that opens with
prose gives the reader nothing to decide from until they have read it. So **every** posted
comment — review body, inline finding, reply — opens with a markdown heading, then one
attribution line.

The heading answers "do I care about this one?" before anything else. On the review body
it carries the **round and the verdict**; on a finding it carries the ID, severity and
confidence, which are that finding's verdict. Maintainer decision (davitf, 2026-09-21).

**Review body:**

```
## Round 2 · 🔄 Request Changes

**Claude Code** · `code-review-skill` · `3060ac51` · scope `9f21ab4..3060ac51`
```

Conditional approval names what it is conditioned on in the heading itself:
`## Round 3 · ✅ Approve, conditional on K7, K9`.

**Inline finding:**

```
### K6 · 🟡 `[important]` · `CONFIRMED`

**Claude Code** · `code-review-skill` · round 2 · `3060ac51`
```

**Reply on a thread:**

```
### K6 · still open

**Claude Code** · `code-review-skill` · round 3 · `f65b6d05`
```

Use `## ` in a review body and `### ` in an inline comment or reply. The level is what
keeps the raw markdown readable, and it nests a finding's heading under the body's.

On a PR whose threads mix maintainer questions, `cursor[bot]` dispositions, and a reviewer
posting through the maintainer's account, this is what makes a thread scannable — the
author avatar says `davitf` for two of those three. Keep the attribution to one line; the
detail belongs under it.

### The `review` label is a command

Adding the `review` label to a pull request starts a review round
([`review-loop.md`](../../../../dev-docs/review-loop.md)). A reviewer never adds it: whether
another round is wanted is what your verdict says, and the implementer acts on it.

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
