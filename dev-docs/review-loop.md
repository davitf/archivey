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
job is skipped before a runner starts. A pull request carrying `no-review` gets no round
either, whoever asks; the review hub carries it, because its diff is the whole
repository.

**Adding the label.** The `review` label exists in the repository, and has to: `gh pr edit
--add-label` refuses a label name it cannot find. From a shell,
`gh pr edit <number> --add-label review`. Through the GitHub MCP, whose `issue_write`
`update` *replaces* the whole label set, read the pull request's labels first and write
them back with `review` appended; passing `["review"]` alone removes every other label.

**If the label is still on the pull request a few minutes later, no round started.**
GitHub does not run `pull_request` workflows while a pull request has a merge conflict,
and a merge ref that still carries an older copy of this workflow may not listen for the
label. Adding a label that is already there raises no event, so every later request does
nothing too. Resolve the conflict or merge `main`, then remove the label and add it
again.

**The outcome shows as a label.** A finished round leaves one label saying how it
ended, and takes off any other of the four:

| Verdict | Label |
| --- | --- |
| `clean`, `approved` | `approved` |
| `conditional` | `approved-with-fixes` |
| `findings` | `changes-requested` |
| `decision` | `needs-decision` |

These labels only report, so the pull request list shows where each one stands.
Nothing reads them: the rounds are counted from the closing comments below, because a
status label that steered the rounds is what went wrong with the `loop:round-N` labels
this replaced. A round that did not finish or did not run leaves the label as it was.
A label left on after later pushes is stale; the closing comment names the commit that
was reviewed. `OUTCOME_LABELS` in the gate holds the mapping.

## Counting rounds

Each finished round posts one closing comment from `github-actions[bot]` whose first line
is a `<!-- archivey-review-round n=N sha=… verdict=… -->` marker. The workflow reads
those first lines to number the next round. The verdict is there for whoever reads the
pull request's history, and nothing acts on it. No other round state exists, so nothing
can fall out of step with what actually ran:

- A review that stops before writing its verdict posts "did not finish" with an
  `<!-- archivey-review-attempt … -->` marker instead. It is not a round, but the commit
  is remembered: an agent's retry at the same commit is refused, because a failure that
  repeats every time (the workflow-file mismatch below) would otherwise loop. A new
  commit or a person's label retries.
- A comment that quotes a marker changes nothing, because only the workflow's own
  comments are read.
- If the workflow fails before deciding, it says so on the pull request, and nothing is
  counted.
- Nothing ever runs without a fresh label, so a failed step cannot make a round repeat
  by itself.

## Who is asking

The round cap and the retry guard apply to agents only, so the workflow has to tell an
agent from a person. `sender.type` is not enough: an agent working from a Claude Code project thread
sometimes lands its label as `davitf`, type `User` (#384's events, 2026-09-21), and
sometimes as `claude[bot]`. The workflow reads the `review` labeled event this run is
for instead: the latest one by the same sender, no older than the pull request's
`updated_at` in the webhook (which labelling bumps) less thirty seconds. The events API
can lag the webhook, so it fetches again for a few seconds until that event is listed;
without the date check, an agent's label could be read against the maintainer's own
earlier click. A person is a `User` sender whose event has no
`performed_via_github_app`, which is what a click in GitHub's own interface records. An
event that cannot be found counts as an agent, which can only refuse a person, and a
second click fixes that. `is_person` in the gate holds the rule.

## Stopping it

- **The verdict decides whether another round is asked for.** The reviewer writes one of
  five verdicts. `clean`, `approved` and `conditional` mean it does not need to see the
  result: `approved` is a plain "✅ Approve", and `conditional` covers "✅ Approve,
  conditional on the listed fixes" and "💬 Comment". The findings are still posted in
  full and still fixed, but the closing comment does not ask for another round. `findings` (🔄 Request Changes) asks for one.
  `decision` stops until the maintainer answers. `VERDICT_STOPS` in
  [`scripts/review_loop_gate.py`](../scripts/review_loop_gate.py) holds the mapping.
- **The verdict advises; it does not refuse.** After a verdict that needs no other
  round, an agent's label still runs one. A nit or a maintainer question sometimes grows
  into a larger change that warrants a full review, and the implementer is trusted to
  judge that (davitf, 2026-09-23). The cap below still bounds it.
- **Five rounds an agent can ask for, then a person.** Past `MAX_ROUNDS` an agent's
  label is refused, with a comment saying so, and a person's label still runs a round.
  Past `MAX_FORCED_ROUNDS` (eight) nothing runs, so the bound holds even if the person
  check were ever misread. The cap is the backstop against two agents going back and
  forth, not the mechanism: on the rounds recorded in full (#380: nine findings, then
  three, then two; #389: five, three, two) the verdict stopped both at round 3.
- **A decision.** If block 3 of the review holds a packet
  ([pair-workflow §Decision packet](pair-workflow.md#decision-packet-canonical-escalate-form)),
  the verdict is `decision` and the closing comment carries the question. Once it is
  answered, the implementer acts on the answer and adds `review` to carry on. The
  workflow cannot tell an answered question from an open one, so waiting for the answer
  is the implementer's job.
- **To stop a pull request from being reviewed**, do not label it, or add `no-review`
  to keep it out for good. Nothing else starts a round.

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

The `review` and `no-review` labels exist in the repository, and so do the four outcome
labels. `gh pr edit` cannot create a missing one, so recreate any of them by hand if it
is ever deleted.

## Known rough edges

- **It cannot review a change to its own workflow file.** `anthropics/claude-code-action`
  refuses to run when the calling workflow differs from the copy on the default branch,
  and a `pull_request` run uses the pull request's copy. Such a round ends "did not
  finish", uncounted, and an agent's retry at the same commit is refused. Running on
  `pull_request_target` instead would use `main`'s copy; it is untried with the action
  and would hand secrets to a run that checks out the pull request's code, so it is not
  done here. A change to `review-loop.yml` needs a review from a Claude Code
  session that did not write it, run by hand, so keep the workflow edit small and
  separable. Observed on #379 (2026-09-20).
- **The review reads its own previous rounds from the pull request**, not from a
  handoff. Stable finding IDs make that work
  ([`code-review-skill` §6](../.claude/skills/code-review-skill/SKILL.md)),
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
