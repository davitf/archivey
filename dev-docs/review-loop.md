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
                             │  Cursor says it has finished: out of draft, or a
                             │  comment starting `@claude review`. Failing that,
                             ▼  thirty minutes with no new commit
                  Claude reviews (round N of 3)
                             │
        ┌────────────────────┼──────────────────────────┐
        ▼                    ▼                          ▼
   nothing found      findings, no question      a maintainer decision
   loop:done          ping the implementer       loop:decision
   over to a human    it fixes ─► round N+1      everything stops
```

| Hop | What triggers it | Where it is configured |
| --- | --- | --- |
| Issue → implementation | `@Cursor` in a Linear comment, or assigning the issue to Cursor. Triage rules can do it automatically | Linear, team ARC |
| Implementation → review | The implementing agent taking the pull request out of draft. Failing that, the branch going thirty minutes without a new commit | [`.github/workflows/review-loop.yml`](../.github/workflows/review-loop.yml) |
| Review → addressing | A comment the workflow posts after each round of findings, addressed to whoever holds the branch | Same workflow |
| Addressing → next review | A comment from the implementing agent *starting* with `@claude review`. Failing that, the same thirty minutes of quiet | Same workflow |
| Any step → the maintainer | A `loop:decision` label and a plain-language question on the pull request | Same workflow |

**That ping is the part worth understanding.** Cursor sometimes picks a review up on
its own and sometimes does not, which made the third hop the unreliable one. Asking
explicitly, every round, removes the guessing: the comment names `/address-review` so
the fixing agent lands in
[`address-review-findings`](../.claude/skills/address-review-findings/SKILL.md) rather
than in a generic "fix the comments" posture. Who it addresses depends on the branch —
see [The roles run both ways round](#the-roles-run-both-ways-round).

## The roles run both ways round

Cursor implementing and Claude reviewing is the common direction, not the only one.
Claude also implements — from this project's threads, on a `claude/*` branch — and then
Cursor reviews, because [Claude never reviews its own diff](pair-workflow.md). The loop
is the same loop in both directions; two things had to stop assuming who was who.

**The findings ping is addressed by branch prefix.** After a round of findings the
workflow asks the implementing agent to work through them, and that comment used to say
`@cursor` unconditionally — which, in the reversed direction, handed the fixes to the
agent that had just written them up. It now reads `@cursoragent` on a `cursor/*` branch
and `@claude` on anything else. **The GitHub handle is `@cursoragent`**: on GitHub
`cursor` is the company's organization account, the app posts as `cursor[bot]` and a
bot login cannot be mentioned, so `@cursor` there notifies an org and wakes no agent.
On Linear the handle is `@cursor`, which is where the wrong one came from, and the hop
addresses its own copy of the ping accordingly. The prefix is the signal the gate already uses to decide
enrolment, so there is no new piece of state, and `head_ref` comes out of the gate
alongside the sha the round is reviewing.

**A `cursor/*` ping also goes to the Linear issue, because the GitHub comment alone
does not arrive.** The workflow posts with `GITHUB_TOKEN`, so the ping comes from
`github-actions[bot]`, and a Cursor background agent whose session has already ended
does not wake for it. Measured on #374 (2026-09-20): sixty-six minutes of silence after
the GitHub ping, then fourteen minutes from the same ask posted on the Linear issue to
a push with every finding fixed. The quiet-period fallback cannot rescue this — the
branch is quiet *because* the implementer never learned there was anything to do — so
without the second hop the pull request sits at `loop:round-1` until a person notices.
[`scripts/linear_ping.py`](../scripts/linear_ping.py) is that hop: it asks Linear which
issue holds this pull request as an attachment, so the loop stores nothing new, and it
posts the same comment there with the pull request's URL appended. **That attachment is
created deliberately**, by the agent that opened the pull request, as
[`address-linear-issue`](../.claude/skills/address-linear-issue/SKILL.md) §2 requires;
Linear's own GitHub integration links a pull request from the branch name, title or
description, and none of those may carry a tracker key here. Cursor used to write a
`Linear Issue:` footer into every body it opened, which is what linked them before, and
the script still reads it when no attachment answers — but that footer published a
private tracker link on a public repository, which `AGENTS.md` forbids, so it was
dropped on 2026-09-20 and nothing here wants it back. The hop needs a `LINEAR_API_KEY`
repository secret. Without one it warns and exits 0 — the
findings are already on the pull request by then, and failing a round over a delivery
convenience would be worse than the gap. To check the credential without spending a
round, run the `Linear ping check` workflow: it resolves the issue for a pull request
you name and prints what it would post. The `@claude` direction needs none of this:
that comment is read by whatever session is subscribed to the pull request's activity,
not by a workflow.

**Cursor hands an approval back for a pass from zero** (davitf, 2026-09-19). When
Cursor's verdict is ✅ Approve and Claude implemented, `.cursor/commands/code-review.md`
tells it to post one last comment starting `@claude review`. That is an ordinary round,
not a forced one — the cap and every park still apply to it, as they do to any agent's
comment — and it reads the whole diff rather than a fix-diff: the round counter only
counts rounds this loop ran, so a pull request Cursor has reviewed three times still
arrives at Claude's round 1. A second reviewer's first look is not a re-review. Two
reviewers who have each read the same tree cold are worth more than one that read it
twice.

**That hand-back also enrols the pull request**, which is the one thing it needs that no
other agent comment does. Only `cursor/*` auto-enrols, deliberately, so a `claude/*`
branch reaches the hand-back with no loop label and the bot path's enrolment check
refused it — silently, because the workflow posts nothing when the gate declines. An
explicit request from a trusted agent is a better enrolment signal than a branch prefix,
so it is taken as one. An unenrolled `cursor/*` branch is still refused: Cursor's own
pull requests enrol themselves when they open, so one without a label predates the loop,
and sweeping those in is exactly what keeping the prefix out of the scan avoids.

If that pass finds something real, it is the ordinary loop and not an escalation: the
implementer is pinged and rounds continue under the same cap (davitf, 2026-09-19). One
reviewer disagreeing with another's approval is not by itself evidence of anything, and
stopping for a human there would cost a round trip for what is usually a nit.

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
- **Opening a comment with `@claude review`**, after addressing a round of findings,
  when the pull request is already out of draft and has no second draft transition to
  offer. The phrase counts only at the top of a comment — see
  [Why the phrase has to come first](#why-the-phrase-has-to-come-first).

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
thirty minutes and is not the commit the last round already read. One pull request per
tick, so the first tick after this lands cannot start a review on everything at once.

The timer is the floor, not the mechanism. When the signal arrives the review is
immediate; when it does not, the work still gets reviewed, half an hour later, and
nobody has to notice.

Two consequences that are easy to trip over:

- **Say it last.** A commit pushed after the signal is not in what gets reviewed.
- **Pushing again while you wait pushes the review back**, if you are relying on the
  quiet period.

Draft status is otherwise ignored, and `loop:off` is what takes a pull request out for
good. A draft nobody ever marks ready still gets reviewed once it goes quiet, because
waiting for a human to click a button is the thing the loop exists to avoid.

### Why the phrase has to come first

The gate takes `@claude review` only at the start of a comment, leading whitespace
aside. That is not tidiness: this loop's paperwork quotes its own trigger constantly.
The hand-back comments the workflow posts say "comment `@claude review` to buy another
round". A review packet puts the phrase inside a maintainer question about the phrase.
A dispositions comment quotes it back while explaining what was fixed.

On 2026-09-19 all three shapes were live on #369, the pull request that was building
the loop. The reviewer's packet tripped the guard at 12:15 and the dispositions comment
tripped it at 12:24 — that second one got past the gate as a *forced* round, because it
was posted from the maintainer's account, and started a full Claude review of the pull
request fixing the loop. It was cancelled by hand.

Anchoring separates asking for a round from writing about one. Nobody opens a comment
with the phrase by accident, and an agent told to post it first has no trouble doing so.

The job-level `if:` in the workflow stays the loose `contains()`, on purpose. GitHub
expressions have no anchor and no regex, and `startsWith` would drop a real trigger
whose body begins with a blank line, silently. So the workflow lets anything mentioning
the phrase reach the gate, and the gate — which is unit-tested — decides. A quoting
comment costs a runner for a few seconds and no model tokens, because the review step
sits behind `steps.gate.outputs.run`.

### Who may say it

`@claude review` from a repository collaborator is a *forced* round: it runs past the
three-round cap and past `loop:decision` or `loop:hold`, because a person is who set
those and is entitled to clear them.

The same comment from `cursor[bot]` or `claude[bot]` is not. It starts an ordinary
round, so the cap and every park still hold. GitHub reports `author_association: NONE`
for a bot even on a pull request it has been working on, so the gate recognises these
by login instead — `TRUSTED_BOTS` in `scripts/review_loop_gate.py`. The narrower grant
is deliberate: an agent that fixes, comments, fixes and comments would otherwise run
the loop indefinitely, and "I have stopped pushing" is a statement of fact, not a
request for an exception.

**No comment starts a round past round 6**, whoever sends it — by three different
routes, not one. A collaborator's meets `MAX_FORCED_ROUNDS`; a bot's met the ordinary
cap at round 3 long before; a stranger's was never going to start a round at all. The
ceiling sits inside the collaborator branch rather than ahead of all three, because
`cap_reached` makes the workflow write `loop:done` and rewrite the status comment, and
that is not a write to hand to anyone who can type the phrase.

The case it exists for is that the first two are not actually distinguishable: the
review addendum lets an agent post through the maintainer's account, where it is
`OWNER` like the maintainer, and `MAX_FORCED_ROUNDS` is what stops that from being an
unbounded spend. Six is twice the automatic cap, so a
person asking for one more round will never meet it. Past it, `workflow_dispatch` with
`force` is the override, and that one stays unbounded because a button in the GitHub UI
is not something an agent presses.

**A round that runs clears the parks that predate it.** `loop:decision`, `loop:hold`
and `loop:done` are all removed as the verdict is applied, and the verdict then sets
the real state. Without that the loop restarts exactly once: answering the question and
opening a comment with `@claude review` runs the round, but `loop:decision` survives it and the
round after is refused with nothing on the pull request saying why.

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

`loop:done` marks the end of the **automatic** loop, not the end of what is possible. A
person can always buy another round; that is true at round 3 as much as at round 5. So a
round bought past the cap still counts as the last automatic one and still puts the label
back as it finishes. Treating a bought round as "not the last" instead left a pull request
past the cap with no `loop:done` at all, after the verdict step had cleared the stale
parks — refused by the scan and by every bot, with a status comment promising a round that
could not come.

**Enrolment is opt-in and happens once, when the pull request opens.** A branch named
`cursor/*` enrols itself; anything else needs `loop:on` added by hand, which is what
[`address-linear-issue`](../.claude/skills/address-linear-issue/SKILL.md) does on a
branch of its own. The scan reads only the label, never the branch prefix — a scan that
honoured the prefix would sweep in every `cursor/*` pull request that was already open.
Eight pull requests were open the day this landed, and a loop that reviewed all of them
would have been switched off within the hour.

One thing the labels do not record is *which commit* a round read, and without it the
scan would review the same head on every tick forever. That lives in the status
comment's HTML marker (`<!-- archivey-review-loop-status sha=… -->`), which the loop
already has to keep current, so there is no second piece of state to forget.

It records only what a round actually read. A review that dies before reaching a
verdict writes no sha, so clearing the `loop:hold` it leaves behind is enough to make
the scan try that commit again; recording it there would have marked the head read and
skipped it for good.

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
- **Answer, then restart.** Reply in the review thread, then post a comment that
  *starts* with `@claude review`.
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
guard and the two split the traffic.

The requirement is that they never both fire, which means the loop's trigger has to be
no looser than `claude.yml`'s skip. It is now strictly tighter: `claude.yml` skips any
comment holding the phrase anywhere, and the loop takes it only at the top.
`tests/test_review_loop_gate.py` asserts that one direction over a table of near misses
— nothing the gate accepts is something `claude.yml` would also answer. GitHub
expressions have no regex, which is why `claude.yml`'s half stays a plain,
case-insensitive substring; re-running the App installer overwrites that file and drops
the guard entirely.

**The gap between the two is a cost a writer should know about.** A comment that quotes
the phrase mid-sentence runs neither workflow: the loop ignores it because it is not at
the top, and the general assistant ignores it because `contains()` sees the phrase
anywhere. Not starting a round is the point
([Why the phrase has to come first](#why-the-phrase-has-to-come-first)). Losing the
assistant is the side effect, and it is silent — a comment that asks `@claude` a
question *and* quotes the trigger phrase gets no answer and no explanation. Quote the
phrase or ask the assistant, not both in one comment.

**`claude.yml` also answers only people now.** It passes no `allowed_bots`, so a
bot-authored `@claude` can do worse than nothing: the action aborts with "Workflow
initiated by non-human actor" and leaves a red check on the pull request. That is how
#369 got one — `cursor[bot]` left a review comment mentioning `@claude` while discussing
this loop.

Whether it aborts or skips is the action's own call, and not one the workflow can
predict. `anthropics/claude-code-action` decides separately from the `if:` whether a
mention is addressed to it, and logs `No trigger was met for @claude` when it is not; a
run that gets that far skips and goes green whoever triggered it. Both shapes appeared on
#369 inside twenty minutes, from the same two accounts. That unpredictability is the
reason to guard rather than to rely on it. Each branch of the guard now requires `user.type != 'Bot'`, and
the phrase exclusion, which used to sit on the `issue_comment` branch alone, is on every
branch that reads a body. Naming the bots in `allowed_bots` would have been the wrong
repair: it makes those runs execute rather than abort, which is more agent usage, not
less.

That guard is also what lets the findings ping address `@claude` on a `claude/*` branch.
The workflow posts it as `github-actions[bot]`, so it reaches the session watching the
pull request without waking the general assistant on the way past.

## Bots trigger almost everything here, and must be named

`anthropics/claude-code-action` refuses to run when the workflow was initiated by a bot
unless that bot is listed in `allowed_bots`. Every automatic path into this loop is a
bot: Cursor pushes and opens pull requests as `cursor[bot]`, and an agent working in
this repository marks them ready as `claude[bot]`. The first real round failed two
seconds in for exactly this reason, and it stayed hidden until then because an earlier
draft guard had refused every run before it reached the action.

The list names six entries rather than using `*`: `cursor`, `claude` and
`github-actions`, each in both spellings, because the action's own refusal prints the
bare name while the event carries the `[bot]` suffix. `github-actions[bot]` is there for
the scheduled scan, which is the path the whole fallback rests on and the one no person
ever initiates. The point of the setting is that an unexpected bot cannot spend review
credits, and the gate's own guards — forks, enrolment, the cap — sit behind the action,
not in front of it.

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
opening a comment on it with `@claude review`.

## Known rough edges

- **The loop cannot review a change to its own workflow file.**
  `anthropics/claude-code-action` refuses to run when the workflow calling it differs
  from the copy on the default branch — "the workflow file must exist and have
  identical content to the version on the repository's default branch" — so a pull
  request that edits `.github/workflows/review-loop.yml` gets an action that skips
  without a verdict. The loop then does the right thing with that: no round is counted,
  `loop:hold` parks the pull request, and the status comment says the review did not
  finish. But `@claude review` cannot rescue it, because the next attempt fails
  identically. Such a pull request has to be reviewed by Cursor or by a person, and it
  is worth splitting one so the workflow edit is small and separable. Observed on #379
  (2026-09-20). The same rule is why a new `workflow_dispatch` workflow cannot be run
  before it merges: GitHub only dispatches workflows present on the default branch.
- **The findings ping was addressed to an organization rather than an agent.** It said
  `@cursor`, which on GitHub is the company's org account; the app posts as
  `cursor[bot]`, a bot login cannot be mentioned, and the account an agent answers to is
  the user `cursoragent`. Fixed in
  [the ping section above](#the-roles-run-both-ways-round) on 2026-09-20 (davitf spotted
  it). This also undercuts the measurement the Linear hop below was built on: #374's
  sixty-six minutes of silence were after a ping that mentioned nobody. A human comment
  reading `@cursoragent please review` on #372 got "Taking a look!" from `cursor[bot]`
  seven seconds later, so the handle alone may be the whole of it. What is still
  untested is the right handle from a bot account, and the next `cursor/*` round
  measures it.
- **The findings ping has a Linear hop as its fallback**, needing the `LINEAR_API_KEY`
  secret, set on 2026-09-20. It stays until the line above is settled: it is cheap, it
  never fails the run, and posting on the Linear issue is the one path measured to wake
  an agent whose session had ended. Without the secret, every `cursor/*` round ends with
  a warning in the job summary and a pull request nobody has told the implementer about;
  the manual workaround is to post the findings summary as a `@cursor` comment on the
  Linear issue by hand — `@cursor` is right *there*, which is where the wrong GitHub
  handle came from. Carry the findings in that comment rather than pointing at the pull
  request.
  The other route considered — posting the GitHub comment from a personal access token
  so it arrives from a human account — is cheaper to wire, but it does not match the
  path that was actually observed to work, and it spends a token.
  **The hop is written but not yet exercised**, for the reason in the bullet above: the
  `Linear ping check` workflow that would prove the credential end to end cannot be
  dispatched until it is on the default branch.
- **Thirty minutes is a guess**, and it started as ten. It only matters when an agent
  does not send the signal. The change (davitf, 2026-09-19) was about which way to be
  wrong: a premature round spends one of three on half-written code, while a late one
  only delays a branch nobody is watching. Ten minutes was short enough that an
  ordinary pause — a long test run, a slow tool call, a session waiting on a person —
  read as "finished". `QUIET_MINUTES` in the gate is the one place to change it.
  **The clock is the committer's, not the push's**: the scan reads the head commit's
  `committer.date`, because GitHub carries no per-commit push time it can reach. Since
  `CONTRIBUTING.md` asks for a long gate run before pushing, a commit made at 12:00 and
  pushed at 12:40 already counts as forty minutes quiet when it lands. Closing that
  needs a record of when the scan first saw a head, which is a second piece of
  per-commit state; what makes it not worth carrying yet is that a branch the clock
  misjudges is one whose agent did not send the finish signal.
  **The cron interval is a separate knob and does not move with it**: the schedule is
  how often the state is checked, the quiet period is how long a branch must have been
  still. Checking every ten minutes keeps the fallback responsive once a branch does
  qualify; it costs nothing, because a tick that finds nothing eligible is the gate
  declining in seconds with no model call.
- **Cursor does send the signal.** Observed on #374 (2026-09-20), three rounds out of
  three: it pushed, posted its dispositions, and commented `@claude review` within
  fifteen seconds each time, and the round started on that comment rather than on the
  timer. The quiet period stays as the floor, but it has not had to catch anything yet.
- **Every scheduled tick shares one concurrency group**, `scan`, and a round can take
  the full 45-minute timeout. So a scheduled round blocks every later scheduled tick
  until it finishes, including ticks for other pull requests, and GitHub keeps only the
  most recent of the ones that queue behind it. That is the intended trade — the
  fallback has to be safe, not fast, and a comment-triggered round has its own group
  keyed on the pull request — but it means the floor is one pull request per *review*,
  not one per tick.
- **A scheduled round and an `@claude review` on the same pull request can overlap.**
  They are in different concurrency groups, and the scan records the commit it read
  only once its round finishes. Two reviews of one commit is wasteful but harmless, and
  the alternative — a lock — is more machinery than the failure justifies.
- **The review reads its own previous rounds from the pull request**, not from a
  handoff. Stable finding IDs are what make that work
  ([addendum §10](../.claude/skills/code-review-skill/reference/archivey-review-addendum.md)),
  so renumbering between rounds breaks the status table the next round opens with.

  Each round is a fresh session with no memory of the last one, which is deliberate —
  a reviewer that remembers proposing a fix is a poor judge of that fix, for the same
  reason the implementer does not review its own diff. The cost is that everything
  round 2 knows has to be written down on the pull request by round 1. Two rules in
  §10 close the gap that leaves: round 2 reads its own earlier *review bodies* rather
  than only the still-open threads, because a resolved thread drops out of the default
  view while the body stays; and it traces each fix outward for a moved contract rather
  than trusting the fix-diff, because a fix that is correct in isolation and wrong for
  one caller is exactly what the narrow scope would otherwise hide. Considered and
  rejected (davitf, 2026-09-19): keeping one warm session subscribed to the pull request
  across rounds. It buys memory at the price of the cold judgement above, and the memory
  is the unreliable half — the container is reclaimed after a period of inactivity and
  long context is compacted lossily, both of which bite hardest in the slow cases where
  the memory would have mattered most.
