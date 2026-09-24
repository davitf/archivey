---
name: code-review-skill
description: |
  Archivey's review skill: how to review a pull request, a fix round, an OpenSpec
  proposal, a whole-file sweep or a commissioned deep review, and how to post the result.
  Also reachable from Cursor via the project command in `.cursor/commands/code-review.md`.
  Use when: reviewing pull requests, conducting PR reviews, code review, reviewing code changes,
    re-reviewing fixes, reviewing proposals or design docs, sweeping files, security or
    architecture reviews, finding bugs, giving feedback on code,
    or when the user invokes /code-review-skill.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash      # reproduce a finding or check a runtime claim — not to re-run the gates (§6)
  - WebFetch  # look up current docs and best practices
---

# Code Review Skill

This file holds what every review in this repo needs: how to report, the output shape,
verdicts, severity, and how to post. What to *do* depends on what you are reviewing, and
each kind has its own doc (§1). Read this file, then that one — nothing else is required
reading.

> Started as a Python-only subset of
> [awesome-skills/code-review-skill](https://github.com/awesome-skills/code-review-skill)
> (MIT). The repo's own rules outgrew it and now live here; the upstream-derived guides
> remain as optional reference (§1), under the original `LICENSE`.

**Invoke it by name: `/code-review-skill`.** A bare `/code-review` is a *builtin* skill in
both Claude Code and Cursor — different output shape, and it will edit code. Cursor's
[`.cursor/commands/code-review.md`](../../../.cursor/commands/code-review.md) routes
`/code-review` here; everywhere else ask for `/code-review-skill` explicitly.

**Repo default — report findings, edit nothing** unless the user asks. Output is markdown
prose in the three-block shape (§3), never a host-specific findings tool. The review is
the handoff: do not also produce a prompt or brief for whoever fixes the PR — they run
`address-review-findings` off the PR threads.

## 1. What are you reviewing?

| Reviewing | Read | Why it is separate |
|---|---|---|
| A code PR, first look (round 1, or a reviewer new to the PR) | [`reference/code-pr.md`](reference/code-pr.md) | Two passes — code cold, then context — and the archivey checklists |
| A code PR again, after fixes (round 2 on) | [`reference/fix-round.md`](reference/fix-round.md) | Scope is the fix-diff, not the PR; 67 of the 70 findings raised after round 1 in 2026-09-23/24 came from the previous round's fix |
| An OpenSpec proposal, delta spec or `design.md` | [`reference/reviewing-proposals.md`](reference/reviewing-proposals.md) | No code tree to read cold, so values first |
| Whole files rather than a diff (a sweep batch on #315) | [`reference/whole-file-sweep.md`](reference/whole-file-sweep.md) | One `SWEPT` marker per file, findings or not |
| A commissioned `review/` brief | [`reference/deep-reviews.md`](reference/deep-reviews.md) | A different deliverable and a baseline you record yourself |

**Also read [`CONTRIBUTING.md`](../../../CONTRIBUTING.md) at the start of a first look.**
It holds the coding and testing rules; `code-pr.md` says which of them PRs here break and
does not restate them.

**Authoritative sources — open when a finding touches them:**
[`VISION.md`](../../../VISION.md) (product tie-breaker),
[`openspec/specs/`](../../../openspec/specs/) (capability contracts, revisable when wrong),
[`dev-docs/threat-model.md`](../../../dev-docs/threat-model.md) (trust boundaries and open
gaps), [`review/README.md`](../../../review/README.md) and
[`review/STATUS.md`](../../../review/STATUS.md) (deep-review conventions and live triage).

**Optional guides**, upstream-derived and generic — open one only when a finding needs it:
[Python](reference/python.md) ·
[Architecture](reference/architecture-review-guide.md) ·
[Performance](reference/performance-review-guide.md) ·
[Security](reference/security-review-guide.md) ·
[Universal quality](reference/code-quality-universal.md) ·
[Common bugs](reference/common-bugs-checklist.md) ·
[Error handling](reference/cross-cutting/error-handling-principles.md) ·
[Concurrency](reference/cross-cutting/async-concurrency-patterns.md) ·
[Best practices](reference/code-review-best-practices.md).
The fill-in report form is [`assets/pr-review-template.md`](assets/pr-review-template.md).

> Large diff? `git diff main...HEAD | python scripts/pr-analyzer.py` triages complexity
> before you read.

## 2. Finding discipline

Archivey reviews optimize for **maximum code quality with a human maintainer as the
filter** — not for an automated gate minimizing false positives. So **over-report on
existence, be rigorous on labeling.** Raise the concern; never suppress a real one
because you're unsure. The discipline is honest labeling, not silence.

The two-axis + reclassification model below is richer than any host's findings schema,
which is why the output is prose. Posting to a PR → §6.

### Two axes: severity ≠ confidence

Rate every finding on both axes so the maintainer reads **severity × confidence** and
decides:

- **Severity** — impact *if the finding is real*: 🔴 `[blocking]` / 🟡 `[important]` /
  🟢 `[nit]` (plus 💡 / 📚 / 🎉 non-blocking). Mapping for this repo: §5.
- **Confidence** — `CONFIRMED` (you traced the actual failing path: definitions, callers,
  guards) / `PLAUSIBLE` (real risk, not fully traced or no repro built) / `DISPROVEN` (you
  traced it and the code is correct — **route it, don't delete it**).

Low confidence lowers the *confidence tag*, never the decision to report: a
🔴 `PLAUSIBLE` finding is still reported.

### Verification routes findings; it never silently culls them

After the code + context passes, re-trace each candidate against the actual path —
definitions, callers, guards — rather than pattern-matching. Tracing changes the tag and
may *reclassify* — it does not delete real concerns. When a finding comes back `DISPROVEN`,
ask *"why did I, reading carefully, think this was broken?"*:

- A careful reader could reasonably have misread it → the code isn't self-documenting.
  Re-file as 🟡 **clarity / doc-debt** (`code-pr.md`'s documentation-debt rule) — a comment,
  clearer name, or an `assert` that encodes the invariant.
- Tracing revealed a genuine but non-obvious invariant → 💡 / 📚: suggest the comment or
  assertion that would have made it obvious.
- A careless misread ordinary attention would have avoided → drop it; a 🟢 nit is fair if
  it still cost real review effort.

Only a careless self-misread is ever dropped. Everything else becomes a (possibly smaller)
finding.

### Failure scenario: requested, not gating

Name a concrete trigger where you can — input / archive / state → wrong result, crash, or
contract violation. `CONFIRMED` + repro ⇒ flag it as a **red–green regression-test
candidate** (CONTRIBUTING wants red–green for bug fixes; `code-pr.md` §Testing). Can't
build one → the finding still stands, tagged `PLAUSIBLE` / needs-repro. A missing repro
lowers confidence, never existence.

### Keep findings disciplined (inside block 2)

Over-reporting fails only when it is *unlabeled*. Hold the noise down by discipline, not
suppression:

- **Dedupe by root cause** — one finding per cause; cite one site, list the rest.
- **Rank** by severity, then confidence within a tier.
- Briefing (block 1) stays thin and carries the index; detail lives in each finding's own
  thread (block 2); decisions (block 3) stay only what needs you.

## 3. Output shape — three blocks (required)

Three blocks, and **they do not all go in the same place.** Blocks 1 and 3 are the review
body; block 2 is the inline threads, one per finding (§6). The body is therefore a
summary with an index, and the detail sits on the line it concerns.

That split is a maintainer decision (davitf, 2026-09-21): *"I'm finding PR review comments
too long and hard to find the relevant info."* A body that also carries every finding in
full is the thing being fixed here — the detail is not deleted, it moves to the thread
where it can be replied to and resolved.

The maintainer often has **not** read the diff: design the body for that reader, and the
thread for the implementor. [`assets/pr-review-template.md`](assets/pr-review-template.md)
is the fill-in form.

**Every posted comment opens with a header carrying the round and the verdict** — review
body, inline finding and reply alike. Exact shapes: §6 "Open every comment with a header".

**No tables in anything you post.** They wrap into unreadable columns on a phone, which is
where the maintainer reads them. Bullets instead, one per row. This is about *posted*
comments: the tables in this file and in the rest of the repo are unaffected.

**Brevity fence.** Short form applies only to how blocks 1 and 3 are *presented*. It must
not reduce review depth (the full passes in the review type's doc, and the values check
where the change moves a contract; same tracing and checklists), finding discipline
(over-report on existence; severity × confidence), the specificity of a finding **in its
thread**, or real pause-and-ask items in block 3 — when unsure whether something needs a
human call, include it and label confidence. If block 1 is short because the analysis was
thin, that is a failed review, not compliance with this shape.

#### 1. Maintainer briefing (read this first)

For the maintainer skimming without the code open. Half a screen unless the change is huge.

- **What this change is** — 2–4 sentences: intent, main files/areas touched, behaviour
  delta, in plain language. Assume no familiarity with the PR or the OpenSpec change name.
  **Once per PR, not once per reviewer.** Write it only if no review on this PR carries one
  yet. The maintainer has already read it otherwise, and that is true whether the previous
  review was yours or the other reviewer's — a second reviewer's first look is not a
  re-review under `fix-round.md`, but the description is just as redundant. Any later
  review opens with the status bullets over whatever IDs already exist (`fix-round.md`)
  instead. The one exception is a rework that made the earlier description wrong: then give
  one line on what changed, not a fresh description.

  (Fix-diff scope, `fix-round.md`, stays per *reviewer* — a second reviewer has not read
  the tree and reads `main...HEAD`. It is only the description that is per PR.)
- **Findings** — the index, and the only list of findings in the body. One bullet each,
  ranked by severity then confidence, including 🟢 and 💡: ID, severity, confidence, the
  gist in one sentence, the location, and a link to the thread. Nothing else. The full
  finding is in the thread, and a body that repeats it is the length problem this shape
  exists to fix. **Post the inline comments first, then the body**, because a review
  submitted in one call creates both at once and the body would have no URLs to link to
  (`POST .../pulls/{n}/comments` returns each comment's `html_url`, and §6 records that
  a review body cannot be edited afterwards through the MCP tools). A finding whose thread
  URL you genuinely cannot get is listed with its `file:line` alone rather than held back.
- **Snapshot** — one line: size (approx. lines / small|medium|large), the scope you
  reviewed, and gates (CI status, §6). The verdict is already in the header, so the
  Snapshot does not restate it.
- **Pass 1 (cold)** — first look at a code PR only: one line naming what the code alone did
  not explain before you opened the PR body or the design, each with its finding ID, or
  `nothing`. It makes the cold read checkable; the rule and why it exists are in
  [`code-pr.md`](reference/code-pr.md) §Pass 1.
- **What's fine** (optional, 1–3 bullets) — load-bearing things that looked correct, so the
  briefing isn't only negatives. Put it inside the collapsed block (§6) rather than in the
  running text.

Zero findings worth action? Say so here and keep blocks 2–3 minimal (`None.`).

#### 2. Implementor handoff (goes on the PR)

For whoever fixes or replies. **This block is not text in the review body — it is the
inline threads**, one per finding, anchored on the line it concerns (§6). The body carries
only the index from block 1.

Write each thread to be read months later by someone who has only that thread in front of
them: the finding's own header (§6), then what is wrong, why it matters, the fix
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
(`CONFIRMED` + trigger ⇒ red–green candidate, `CONTRIBUTING.md` §Testing standards). Don't
restate the diff, narrate your process, or pad with transitions. Terse is not thin:
cutting evidence to look brief violates the brevity fence, cutting prose does not.

Every finding gets a thread, 🟢 nits and 💡 suggestions included. Ranking happens in the
index; a thread is ranked by where it sits in the file.

#### 3. Maintainer decisions (your attention)

**Only** items that need a human call — this block is the maintainer UI; block 2 is worker
mail for whoever addresses the PR. Use the canonical decision packet in
[`dev-docs/pair-workflow.md`](../../../dev-docs/pair-workflow.md) §Decision packet —
same six fields, same cold-start test. Do not invent a shorter parallel list. Routine
"please add a test for X" fixes belong in block 2. Nothing to decide → `None.` Do not
invent filler questions, and do not drop a real decision gap to keep the section empty.

Posting for a split implementor/maintainer workflow: blocks 1–2 go on the PR; if you also
chat with the maintainer, send **block 3 packets only** unless they ask for the full
handoff.

**The posted review is the whole handoff — do not also write a prompt for the implementing
agent.** No “paste this into a fresh agent” brief, no prompt file, no second copy of the
findings in chat or in a scratch document. The implementing agent runs
[`address-review-findings`](../address-review-findings/SKILL.md) and reads the PR
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
[`address-review-findings`](../address-review-findings/SKILL.md) §6; both exist because
a settled decision that lives only in a chat transcript is lost — a fresh container has no
memory of earlier sessions (`CLAUDE.md`).

Reviewing an OpenSpec proposal (`reviewing-proposals.md`) uses the same three blocks: "what
this change is" summarizes the proposal's intent, and block 2 is the handoff for whoever
revises the proposal or implements it later.

## 4. Verdicts and the round budget

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
([`dev-docs/review-loop.md`](../../../dev-docs/review-loop.md) §Stopping it). So the
verdict line is a decision about spending another review, and writing 🔄 to keep a round
in hand spends one on wording you have already described.

## 5. Severity

🔴 `[blocking]` must fix · 🟡 `[important]` should fix, discuss if you disagree ·
🟢 `[nit]` small, but still fixed on this PR — *small*, not *optional*.
Non-blocking annotations: 💡 `[suggestion]` · 📚 `[learning]` · 🎉 `[praise]`.
Pair each finding with a confidence tag — `CONFIRMED` / `PLAUSIBLE` / `DISPROVEN` (§2).

| Label | Archivey examples |
|-------|-------------------|
| 🔴 `[blocking]` | Path escape on default extract; catch-all exception translation; core grows a hard dep; unjustified type suppressions; silent solid O(n²) on a common API; deliberate new debt with no recorded decision; public contract change left undocumented *and* undiscussed |
| 🟡 `[important]` | Dishonest cost signal; format parity hole without docs; missing red-green test for a bugfix; threat-model gap touched but unaddressed; CLI forced to import `internal/`; code contorted to match a questionable spec without raising a revision; non-obvious logic that only makes sense after reading OpenSpec/`design.md`/long PR prose (pass-1 doc debt, `code-pr.md`) |
| 🟢 `[nit]` | Naming, comment polish, non-user-facing refactor suggestions — *small*, not *optional*: still fixed on this PR (§4) |
| 💡 / 📚 / 🎉 | Alternatives, teaching notes, praise — non-blocking |

When unsure whether something is 🔴 vs 🟡: **does it undercut a VISION claim or the
error/safety contract?** If yes → 🔴.

## 6. Posting the review to a pull request

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
  thread (§3). Do not also paste the findings into it.
- Findings without a location — process, missing coverage, contract drift across documents
  — stay in the top-level body, still with IDs, and there they are written in full.

Where the host cannot post inline comments, one top-level comment is acceptable, but the
IDs are not optional.

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

### Do not re-run the gates — or re-measure what a previous round recorded

The implementer and CI already ran `check.sh` / `test.sh`. A review does **not**
re-run ruff, pyrefly, ty, or the test suite. Glance at CI in logistics (`code-pr.md`) only
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

**Exception — commissioned `review/` briefs** (`deep-reviews.md`): baseline first still
holds. No CI run to inherit, and the skip count is itself evidence.

### You cannot post a GitHub approval — expected, not news

Agents here post through the maintainer's own account (the same fact "Attribution" below is
about), and GitHub refuses `event: APPROVE` on your own pull request:

```
422 Unprocessable Entity — Can not approve your own pull request
```

This is the normal, permanent state of this repo's review loop, not a failure to report.
So:

- **Submit the review with `event: COMMENT`** (`pull_request_review_write`, method
  `submit_pending`). `REQUEST_CHANGES` is rejected on your own PR for the same reason —
  `COMMENT` is the only event that goes through.
- **Carry the verdict in the text**, where the header already puts it. The
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

### The `review` label is a command

Adding the `review` label to a pull request starts a review round
([`review-loop.md`](../../../dev-docs/review-loop.md)). A reviewer never adds it: whether
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

`/code-review-skill` reports; it does not edit (the repo default above). Leaving the fix to
the implementing agent is what keeps the review a second opinion rather than a self-graded
one.
