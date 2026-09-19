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
Linear issue ──@Cursor──► Cursor implements ──► opens a pull request
                                                      │
                             ┌────────────────────────┘
                             ▼
                  Claude reviews (round N of 3)
                             │
        ┌────────────────────┼──────────────────────────┐
        ▼                    ▼                          ▼
   nothing found      findings, no question      a maintainer decision
   loop:done          `@cursor` comment          loop:decision
   over to a human    Cursor pushes ─► round N+1  everything stops
```

| Hop | What triggers it | Where it is configured |
| --- | --- | --- |
| Issue → implementation | `@Cursor` in a Linear comment, or assigning the issue to Cursor. Triage rules can do it automatically | Linear, team ARC |
| Implementation → review | The pull request opening, and every later push to it | [`.github/workflows/review-loop.yml`](../.github/workflows/review-loop.yml) |
| Review → addressing | An `@cursor` comment the workflow posts after each round of findings | Same workflow |
| Addressing → next review | Cursor's push, which is a `synchronize` event | Same workflow |
| Any step → the maintainer | A `loop:decision` label and a plain-language question on the pull request | Same workflow |

**The `@cursor` comment is the part worth understanding.** Cursor sometimes picks a
review up on its own and sometimes does not, which made the third hop the unreliable
one. Asking explicitly, every round, removes the guessing: the comment names
`/address-review` so the fixing agent lands in
[`address-review-findings`](../.claude/skills/address-review-findings/SKILL.md) rather
than in a generic "fix the comments" posture.

## Round state is labels

There is no database. The loop's whole memory is the labels on the pull request, which
means it is visible in the GitHub UI and a human can change it without a commit.

| Label | Meaning |
| --- | --- |
| `loop:round-N` | Round N has been reviewed. The highest one is the count |
| `loop:on` | Opt a pull request in whose branch name did not enrol it |
| `loop:off` | Never run here. Beats everything, including an explicit request |
| `loop:decision` | Parked on a maintainer decision |
| `loop:hold` | Parked by a human, or by a review that failed before reaching a verdict |
| `loop:done` | Finished: a clean review, or the round cap spent |

**Enrolment is opt-in and happens once, when the pull request opens.** A branch named
`cursor/*` enrols itself; anything else needs `loop:on`. A pull request that was already
open when this landed has no round label, so a push to it does nothing — which is the
point. Eight pull requests were open the day this was written, and a loop that reviewed
all of them on their next push would have been switched off within the hour.

`scripts/review_loop_gate.py` makes that decision, on facts the workflow collects for
it, with no GitHub access of its own. That split is so the rules are testable:
`tests/test_review_loop_gate.py` covers the cap, each parked label, forks, drafts, and
a stranger commenting `@claude review`.

## Stopping it

- **One decision at a time.** Block 3 of the review is the escalation channel and its
  shape is fixed ([pair-workflow §Decision packet](pair-workflow.md#decision-packet-canonical-escalate-form)).
  If a round produces any block-3 packet at all, the loop parks — findings and all —
  rather than letting Cursor guess at the answer while the question is open. The
  status comment carries the question in plain language; the packet with the options
  and their costs stays in the review.
- **Answer, then restart.** Reply in the review thread, then comment `@claude review`.
  That clears the park and buys a round, including a fourth one past the cap.
- **Three rounds, then a person.** Round 4 does not start on its own. If three rounds
  of review and fixes have not converged, another round is not the missing ingredient.
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
by the first `claude.yml` run, which reached the model rather than failing at setup.

Should it ever need redoing by hand: `claude setup-token`, then Settings → Secrets and
variables → Actions. `ANTHROPIC_API_KEY` works in its place if per-token billing is
preferred; swap the input name in the workflow.

The loop labels are created on first use by the workflow, so there is nothing to set up
by hand.

## Known rough edges

- **Whether Cursor answers an `@cursor` from `claude[bot]`** has not been observed yet.
  If it turns out to ignore bot comments, the fallback is the same comment from a
  personal access token, or a `@Cursor` comment on the Linear issue instead.
- **Every push counts as a round**, including a rebase or a typo fix. That is the
  simplest rule that cannot silently skip a real round, and the escape hatch for
  spending the cap too fast is `@claude review`.
- **The review reads its own previous rounds from the pull request**, not from a
  handoff. Stable finding IDs are what make that work
  ([addendum §10](../.claude/skills/code-review-skill/reference/archivey-review-addendum.md)),
  so renumbering between rounds breaks the status table the next round opens with.
