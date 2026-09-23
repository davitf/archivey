# The review loop

Steps 4–6 of the [pair workflow](pair-workflow.md) — implement, review, address — run
through one label. This page says what is wired up, what stops it, and what stays
manual.

## How it works

**Adding the `review` label to a pull request runs one review round.** The round takes
the label off as it starts, reviews, and posts one closing comment that says what the
review found and what happens next.

```text
implementer opens the pull request ──► adds `review`
                                          │
                        ┌─────────────────┘
                        ▼
             a Claude session that did not write the diff
             reviews it from zero (round N) and writes a verdict
                        │
      ┌─────────────────┼──────────────────────────┐
      ▼                 ▼                          ▼
 nothing to see    a 🔴, or a fix to re-read   a maintainer decision
 again: clean, or  the implementer fixes,      the question goes on the
 approved with     pushes, adds `review`       pull request; nobody adds
 nits to fix       again ─► round N+1          `review` until it is answered
 over to a human
```

| Hop | What does it | Where |
| --- | --- | --- |
| Implementation → review | The implementing agent adds `review` when its pull request is ready ([`address-linear-issue`](../.claude/skills/address-linear-issue/SKILL.md) §3). A person can add it at any time | GitHub label |
| Review → addressing | The review itself, plus the closing comment addressed to whoever holds the branch. A Claude Code session subscribed to the pull request's activity reads both | [`.github/workflows/review-loop.yml`](../.github/workflows/review-loop.yml) |
| Addressing → next review | The implementing agent adds `review` again, after pushing, when the closing comment asked to see the fixes ([`address-review-findings`](../.claude/skills/address-review-findings/SKILL.md) §7) | GitHub label |
| Any step → the maintainer | A plain-language question in the closing comment, with the options in the review | Same workflow |

Only someone with triage access can label a pull request, so the label is also the
permission check. A fork gets no round: its `pull_request` run has no secrets, and the
job is skipped before a runner starts.

## Counting rounds

Each finished round posts one closing comment from `github-actions[bot]` whose first line
is a `<!-- archivey-review-round n=N sha=… -->` marker. The workflow counts those
comments to number the next round. No other round state exists, so nothing can fall out
of step with what actually ran:

- A review that stops before writing its verdict posts "did not finish" without the
  marker. It does not count, and adding the label again retries it.
- A comment that quotes the marker changes nothing, because only the workflow's own
  comments are counted.
- Nothing ever runs without a fresh label, so a failed step cannot make a round repeat
  by itself.

## Stopping it

- **The verdict decides whether another round is asked for.** The reviewer writes one of
  four verdicts. `clean` and `approved` mean it does not need to see the result:
  `approved` covers "✅ Approve", "✅ Approve, conditional on the listed fixes" and
  "💬 Comment". The findings are still posted in full and still fixed, but the closing
  comment does not ask for another round. `findings` (🔄 Request Changes) asks for one.
  `decision` stops until the maintainer answers. `VERDICT_STOPS` in
  [`scripts/review_loop_gate.py`](../scripts/review_loop_gate.py) holds the mapping.
- **Five rounds an agent can ask for, then a person.** Past `MAX_ROUNDS` a label added
  by a bot account is refused, with a comment saying so. A label added by a person still
  runs a round. GitHub reports who added a label, and agents working in this repository
  label as bot accounts (`claude[bot]`), so the two cannot be confused. The cap is the
  backstop against two agents going back and forth, not the mechanism: on the rounds
  recorded in full (#380: nine findings, then three, then two; #389: five, three, two)
  the verdict stopped both at round 3.
- **A decision.** If block 3 of the review holds a packet
  ([pair-workflow §Decision packet](pair-workflow.md#decision-packet-canonical-escalate-form)),
  the verdict is `decision` and the closing comment carries the question. Answer it in
  the review thread, then add `review` to carry on.
- **To stop a pull request from being reviewed**, do not label it. Nothing else starts
  a round.

## Bots must be named

`anthropics/claude-code-action` refuses to run a workflow a bot initiated unless that bot
is listed in `allowed_bots`, and an agent adding the label is a bot. The list names
`claude` and `cursor`, each with and without the `[bot]` suffix, because the action
reports the actor both ways depending on the code path. Named rather than `*`, so an
unexpected bot cannot spend review credits.

`.github/workflows/claude.yml`, the general `@claude` assistant, answers only people, for
the reason written next to its `if:`. It no longer needs to share a trigger phrase with
this workflow.

## What stays manual

- **Merging.** Nothing here merges, approves, or pushes to `main`.
- **The review's own judgement.** `code-review-skill` runs with `Read`, `Grep`, `Glob`,
  `Bash` and `WebFetch`. It reports; it does not edit.
- **Reaching the maintainer.** The workflow puts the question on the pull request.
  Carrying it into the project chat, in plain language and one at a time, is a separate
  hop and not GitHub's to make.

## Setup

The Claude GitHub App installer set this up on 2026-09-19: it installed the App, wrote
`claude.yml`, and set the `CLAUDE_CODE_OAUTH_TOKEN` repository secret this workflow reads.
Should it need redoing by hand: `claude setup-token`, then Settings → Secrets and
variables → Actions. `ANTHROPIC_API_KEY` works in its place if per-token billing is
preferred; swap the input name in the workflow.

The `review` label is created implicitly the first time someone adds it.

## Known rough edges

- **It cannot review a change to its own workflow file.** `anthropics/claude-code-action`
  refuses to run when the calling workflow differs from the copy on the default branch,
  and a `pull_request` run uses the pull request's copy. Such a round ends "did not
  finish", uncounted. A change to `review-loop.yml` needs a review from a Claude Code
  session that did not write it, run by hand, so keep the workflow edit small and
  separable. Observed on #379 (2026-09-20).
- **The review reads its own previous rounds from the pull request**, not from a
  handoff. Stable finding IDs make that work
  ([addendum §10](../.claude/skills/code-review-skill/reference/archivey-review-addendum.md)),
  so renumbering between rounds breaks the status bullets the next round opens with.
  Each round is a fresh session with no memory of the last one, which is deliberate: a
  reviewer that remembers proposing a fix is a poor judge of that fix. Keeping one warm
  session across rounds was considered and rejected (davitf, 2026-09-19).

## What this replaced

Until 2026-09-23 the loop ran without a label: a pull request enrolled through
`loop:on` or a `cursor/*` branch, and a scheduled scan every ten minutes started a round
once the branch had been quiet for thirty minutes, alongside an `@claude review` comment
trigger. Round state lived in `loop:round-N` labels, parks in `loop:decision`,
`loop:hold` and `loop:done`, and a Linear hop woke a Cursor implementer whose session had
ended. It was built for Cursor as the implementer, which could not be relied on to ask
for the next round.

It went because Claude now both implements and reviews, and the implementing session
already sees each review arrive, so it can ask for the next round itself. The scan it
leaned on was also barely running: 22 scheduled runs in four days where the cron
promised about 570, against 22 rounds threads started by hand in the same period. And
because the round count lived in labels, a verdict step that failed before applying one
made the scan re-review the same round every ten minutes (#392, 2026-09-21). The
old workflow, its gate and the Linear hop are in git history if Cursor comes back.
