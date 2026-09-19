# The automated review loop

Steps 4–6 of the [pair workflow](pair-workflow.md) — implement, review, address — run
without anyone driving them. This page says what is wired up, what stops the loop, and
what deliberately stays manual.

The loop exists because those three steps are the ones where nothing is being decided.
Handing a review to an implementer and handing the fixes back for re-review is
bookkeeping, and the maintainer was the bookkeeper. What is *not* bookkeeping — the
questions a review cannot answer for itself — is the one thing the loop stops for.

## The circuit

```text
Linear issue ──@Cursor──► Cursor implements ──► opens a draft pull request
                                                      │
                             ┌────────────────────────┘
                             │  Cursor says it has finished: out of draft, or an
                             │  `@claude review` comment. Failing that, ten minutes
                             ▼  with no new commit
                  Claude reviews (round N of 3)
                             │
        ┌────────────────────┼──────────────────────────┐
        ▼                    ▼                          ▼
   nothing found      findings, no question      a maintainer decision
   loop:done          `@cursor` comment          loop:decision
   over to a human    Cursor fixes ─► round N+1   everything stops
```

| Hop | What triggers it | Where it is configured |
| --- | --- | --- |
| Issue → implementation | `@Cursor` in a Linear comment, or assigning the issue to Cursor. Triage rules can do it automatically | Linear, team ARC |
| Implementation → review | The implementing agent taking the pull request out of draft. Failing that, the branch going ten minutes without a new commit | [`.github/workflows/review-loop.yml`](../.github/workflows/review-loop.yml) |
| Review → addressing | An `@cursor` comment the workflow posts after each round of findings | Same workflow |
| Addressing → next review | An `@claude review` comment from the implementing agent. Failing that, the same ten minutes of quiet | Same workflow |
| Any step → the maintainer | A `loop:decision` label and a plain-language question on the pull request | Same workflow |

**The `@cursor` comment is the part worth understanding.** Cursor sometimes picks a
review up on its own and sometimes does not, which made the third hop the unreliable
one. Asking explicitly, every round, removes the guessing: the comment names
`/address-review` so the fixing agent lands in
[`address-review-findings`](../.claude/skills/address-review-findings/SKILL.md) rather
than in a generic "fix the comments" posture.

## A round starts when the implementer says it has finished

A push looks like the end of a piece of work and almost never is. On the first pull
request this loop ever saw, Cursor pushed at 05:27:09, 05:27:52, 05:28:24 and 05:40:26
— four commits, thirteen minutes, all one ticket. A review per push would have spent
the entire three-round cap inside seventy-five seconds, on three snapshots of
half-written code, and had nothing left for the branch as it finally stood. So pushes
are not a trigger at all.

What replaces them is the implementing agent stating outright that it has stopped,
which the repo's own instructions tell it to do as its last action:

- **Taking the pull request out of draft**, after the first implementation. Cursor
  opens drafts anyway, so this costs nothing to ask for.
- **Commenting `@claude review`**, after addressing a round of findings, when the pull
  request is already out of draft and has no second draft transition to offer.

Both start a round immediately. [`address-linear-issue`](../.claude/skills/address-linear-issue/SKILL.md)
and [`address-review-findings`](../.claude/skills/address-review-findings/SKILL.md) §7
carry the instruction, and `.cursor/commands/` repeats it at the entrypoints Cursor
actually reads.

### Why there is still a timer behind it

An instruction is not a guarantee. Agents forget, run out of turn, or finish in a way
that never reaches the last step, and a loop that only starts when an agent remembers
to start it stops silently the first time one does not. So a scan runs every ten
minutes over the pull requests carrying a loop label and reviews the one whose head
commit has been untouched the longest — provided it has been untouched for at least
ten minutes and is not the commit the last round already read. One pull request per
tick, so the first tick after this lands cannot start a review on everything at once.

The timer is the floor, not the mechanism. When the signal arrives the review is
immediate; when it does not, the work still gets reviewed, ten minutes later, and
nobody has to notice.

Two consequences that are easy to trip over:

- **Say it last.** A commit pushed after the signal is not in what gets reviewed.
- **Pushing again while you wait pushes the review back**, if you are relying on the
  quiet period.

Draft status is otherwise ignored, and `loop:off` is what takes a pull request out for
good. A draft nobody ever marks ready still gets reviewed once it goes quiet, because
waiting for a human to click a button is the thing the loop exists to avoid.

### Who may say it

`@claude review` from a repository collaborator is a *forced* round: it runs past the
three-round cap and past `loop:decision` or `loop:hold`, because a person is who set
those and is entitled to clear them.

The same comment from `cursor[bot]` is not. It starts an ordinary round, so the cap
and every park still hold. GitHub reports `author_association: NONE` for a bot even on
a pull request it has been working on, so the gate recognises it by login instead —
`TRUSTED_BOTS` in `scripts/review_loop_gate.py`. The narrower grant is deliberate: an
agent that fixes, comments, fixes and comments would otherwise run the loop
indefinitely, and "I have stopped pushing" is a statement of fact, not a request for
an exception.

## Round state is labels

There is no database. The loop's whole memory is the labels on the pull request, which
means it is visible in the GitHub UI and a human can change it without a commit.

| Label | Meaning |
| --- | --- |
| `loop:round-N` | Round N has been reviewed. The highest one is the count |
| `loop:on` | In the loop. Applied on enrolment; also how you opt a pull request in by hand |
| `loop:off` | Never run here. Beats everything, including an explicit request |
| `loop:decision` | Parked on a maintainer decision |
| `loop:hold` | Parked by a human, or by a review that failed before reaching a verdict |
| `loop:done` | Finished: a clean review, or the last round is spent |

**Enrolment is opt-in and happens once, when the pull request opens.** A branch named
`cursor/*` enrols itself; anything else needs `loop:on` added by hand, which is what
[`address-linear-issue`](../.claude/skills/address-linear-issue/SKILL.md) does on a
branch of its own. The scan reads only the label, never the branch prefix — a scan that
honoured the prefix would sweep in every `cursor/*` pull request that was already open.
Eight pull requests were open the day this landed, and a loop that reviewed all of them
would have been switched off within the hour.

One thing the labels do not record is *which commit* a round read, and without it the
scan would review the same head every ten minutes forever. That lives in the status
comment's HTML marker (`<!-- archivey-review-loop-status sha=… -->`), which the loop
already has to keep current, so there is no second piece of state to forget.

`scripts/review_loop_gate.py` makes every one of these decisions, on facts the workflow
collects for it, with no GitHub access of its own. That split is so the rules are
testable: `tests/test_review_loop_gate.py` covers the cap, the quiet period, each
parked label, forks, the choice between several eligible pull requests, and a stranger
commenting `@claude review`.

## Stopping it

- **One decision at a time.** Block 3 of the review is the escalation channel and its
  shape is fixed ([pair-workflow §Decision packet](pair-workflow.md#decision-packet-canonical-escalate-form)).
  If a round produces any block-3 packet at all, the loop parks — findings and all —
  rather than letting Cursor guess at the answer while the question is open. The
  status comment carries the question in plain language; the packet with the options
  and their costs stays in the review.
- **Answer, then restart.** Reply in the review thread, then comment `@claude review`.
  That clears the park and buys a round immediately, without waiting for a scan,
  including a fourth one past the cap.
- **Three rounds, then a person.** Round 3 says so as it posts, rather than leaving it
  to be discovered later: there is no round 4 to notice. If three rounds of review and
  fixes have not converged, another round is not the missing ingredient.
- **`loop:off`** takes a pull request out permanently.

## Sharing `@claude` with the general assistant

`.github/workflows/claude.yml`, written by the Claude GitHub App installer, answers
`@claude` on any comment. This loop listens to the same `issue_comment` event and wants
`@claude review` for itself, so `claude.yml` carries a `!contains(…, '@claude review')`
guard and the two split the traffic exactly.

"Exactly" is the requirement, not a nicety: a phrase both match starts two agents on one
comment, and a phrase neither matches is a comment that silently does nothing. GitHub
expressions have no regex, so the loop's trigger is the same plain, case-insensitive
substring that `contains()` tests, and `tests/test_review_loop_gate.py` asserts the two
agree on a table of near misses. Re-running the App installer overwrites `claude.yml`
and drops the guard.

## Bots trigger almost everything here, and must be named

`anthropics/claude-code-action` refuses to run when the workflow was initiated by a bot
unless that bot is listed in `allowed_bots`. Every automatic path into this loop is a
bot: Cursor pushes and opens pull requests as `cursor[bot]`, and an agent working in
this repository marks them ready as `claude[bot]`. The first real round failed two
seconds in for exactly this reason, and it stayed hidden until then because an earlier
draft guard had refused every run before it reached the action.

The list names those two rather than using `*`. The point of the setting is that an
unexpected bot cannot spend review credits, and the gate's own guards — forks,
enrolment, the cap — sit behind the action, not in front of it.

## What stays manual

- **Merging.** Nothing in the loop merges, approves, or pushes to `main`.
- **Getting the work started.** A Linear issue still has to exist and still has to say
  something worth implementing.
- **The review's own judgement.** `code-review-skill` runs with `Read`, `Grep`, `Glob`,
  `Bash` and `WebFetch` — it reports, it does not edit.
- **Reaching the maintainer.** The workflow puts the question on the pull request.
  Carrying it into the project chat, in plain language and one at a time, is a separate
  hop and not GitHub's to make.

## Setup

Already done, by the Claude GitHub App installer on 2026-09-19: it installed the App —
which is what gives the review its `claude[bot]` identity — wrote `claude.yml`, and set
the `CLAUDE_CODE_OAUTH_TOKEN` repository secret this workflow reads. Confirmed working
by the OIDC token exchange in the first run that reached the action.

Should it ever need redoing by hand: `claude setup-token`, then Settings → Secrets and
variables → Actions. `ANTHROPIC_API_KEY` works in its place if per-token billing is
preferred; swap the input name in the workflow.

The loop labels need no setting up by hand. The workflow creates them on first use, and
corrects the colour and description of any that already exist — adding a `loop:*` label
through the API creates it implicitly with GitHub's default grey, and the colours are
what make the state readable in the pull request list.

Scheduled workflows only run from the default branch, so the scan does nothing until
this file's workflow is merged to `main`. A branch can still be reviewed before then by
commenting `@claude review` on it.

## Known rough edges

- **Whether Cursor answers an `@cursor` from `claude[bot]`** has not been observed yet.
  If it turns out to ignore bot comments, the fallback is the same comment from a
  personal access token, or a `@Cursor` comment on the Linear issue instead.
- **Ten minutes is a guess.** It only matters when an agent does not send the signal,
  and it is long enough to cover the gaps observed so far and short enough not to feel
  broken. `QUIET_MINUTES` in the gate is the one place to change it; the cron interval
  should move with it.
- **Whether Cursor actually sends the signal** has not been observed yet. If it turns
  out to ignore the instruction, the quiet period is what catches it, which is why the
  timer stays.
- **A scheduled round and an `@claude review` on the same pull request can overlap.**
  They are in different concurrency groups, and the scan records the commit it read
  only once its round finishes. Two reviews of one commit is wasteful but harmless, and
  the alternative — a lock — is more machinery than the failure justifies.
- **The review reads its own previous rounds from the pull request**, not from a
  handoff. Stable finding IDs are what make that work
  ([addendum §10](../.claude/skills/code-review-skill/reference/archivey-review-addendum.md)),
  so renumbering between rounds breaks the status table the next round opens with.
