---
name: address-linear-issue
description: |
  Read a Linear issue, implement the fix, open the PR, and put it in the
  automated review loop, which runs code-review-skill as a separate Claude
  session. Findings come back to the PR and are dispositioned with
  address-review-findings.
  Use when: addressing a Linear issue, implementing a Linear ticket, "fix LIN-123",
  working from a Linear URL, or when the user invokes /address-linear-issue.
---

# Address a Linear issue

Orchestrator only. Review process stays in
[`code-review-skill`](../code-review-skill/SKILL.md) (addendum §10 for posting);
dispositions stay in [`address-review-findings`](../address-review-findings/SKILL.md).
Do not restate those files here.

The review **must** be a second opinion, never this session grading its own
diff (ADR
[0018](../../../dev-docs/decisions/0018-review-and-address-stay-separate-skills.md)).
The [review loop](../../../dev-docs/review-loop.md) is what supplies it: a
GitHub Actions job runs `code-review-skill` in a Claude session that has none of
this one’s context and posts to the pull request. This skill’s job is to get the
pull request into that loop, not to spawn a reviewer of its own.

Runs on Cursor desktop, Cursor Cloud Agent, and Claude Code. The handoff is the
same everywhere, because it happens on GitHub rather than in this session.

## 1. Read the issue

Identify the Linear identifier (`ABC-123`) from the user, a URL
(`linear.app/…/issue/ABC-123`), or conversation context. If it is missing or
ambiguous, ask — do not guess.

If the Linear MCP namespace is `needsAuth`, authenticate, then continue.

Fetch, in this order:

1. Linear `get_issue` with `id` set to the identifier and `includeRelations: true`.
2. Linear `list_comments` with `issueId` set to the same identifier (include
   inline description comments).

No Linear tools → stop and say so. Do not invent the ticket from memory.

Read the title, description, status, labels, relations, and comments before
touching code. The comments are often where the actual acceptance criteria live.

If the issue is a product or contract question rather than an implementation
request, stop and escalate (`address-review-findings` §6 / pair-workflow decision
packet). Do not silently pick a winner when specs, handbook, and the ticket
disagree — that is the standing pause-and-ask rule.

## 2. Fix it

Standard repo loop: `dev-docs/code-map.md` for where to start,
`CONTRIBUTING.md` for tests and gates, `dev-docs/pair-workflow.md` if the change
needs a handbook note or a thin brief. Red–green for bug fixes. Specs and
published docs move with the contract, in the same PR.

Gates **before pushing**:

```bash
./scripts/check.sh --fix
./scripts/test.sh
```

`--all-configs` when extras or versions matter (`CONTRIBUTING.md` §"Before pushing…").

Then push a branch and open a PR. If the issue already names a PR or branch,
continue there instead of opening a second one.

After the PR exists, post a Linear comment on the issue with the PR URL
(`save_comment`). Do not change Linear status unless the user asked.

## 3. Hand the pull request to the review loop

Once the PR is up and the fix is on the remote, put it in the loop and stop.
Do not start this step on uncommitted work, and do not review the diff yourself.

**Open the pull request as a draft, then take it out of draft when you are
finished.** Coming out of draft is what starts the review, and it is the one
signal that cannot be mistimed: everything pushed before it is what gets read.

A branch named `cursor/*` enrols itself when the pull request opens. Anything
else needs the label first:

```bash
gh pr edit <number> --add-label loop:on
gh pr ready <number>
```

That is the whole handoff. `code-review-skill` runs against the PR in a separate
Claude session and posts the review there, with `loop:round-1` on the pull
request. Up to three rounds run.

Two consequences worth stating, because they change what this session does next:

- **Nothing here waits for the review.** It arrives on the pull request minutes
  later, on GitHub, not as a return value. Say in your reply that the loop has
  it, and leave.
- **Stop pushing once you have said you are finished.** If you never say so at
  all, the loop starts a round by itself after the branch has gone thirty minutes
  without a new commit — so a late commit does not lose the review, it only
  delays it and reviews a state you did not mean to submit.

If the loop is not available — no GitHub Actions, or a fork, where the workflow
has no secrets — say so and stop rather than reviewing your own work. A review
this session writes is not a second opinion whatever it is labelled.

## 4. Address the findings

The review lands on the pull request, and the loop posts an `@cursor` comment
asking for it to be addressed through
[`address-review-findings`](../address-review-findings/SKILL.md) — ledger,
reproduce-before-fix, gates, one decision packet at a time, replies on the PR.

So whether **this** session does that work depends on who is still holding the
branch. If you are and the user is waiting on you, read that skill and do it;
the push that follows starts the next round half an hour later, with no further
handoff. If Cursor picked the branch up, leave it alone: two agents pushing to
one branch is worse than a slower round. Steward skips a second round only when
a disposition comment (opener names `address-review-findings`) is already on
those finding IDs. Two agents can still start in the same minute before either
replies; that race is accepted — do not invent a label or marker to close it.

The loop stops itself after three rounds, or the moment a review raises a
question only the maintainer can answer. Neither is this session’s to override.

## Never

- Review your own diff, or “quickly glance” instead of step 3.
- Spawn a reviewer subagent of your own. The loop is the reviewer; a second one
  costs credits to duplicate a review that is already coming.
- Keep pushing to the branch after the handoff “while waiting”. That is what
  stops the review from starting.
- Implement from the title without reading Linear comments.
- Skip posting the PR URL on the Linear issue.
- Mark the Linear issue done, or close it, unless the user asked.
- Run `code-review-skill` in this session and then pretend a second agent did it.
