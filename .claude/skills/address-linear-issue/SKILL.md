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

After the PR exists, do two things on the Linear issue. Do not change its status
unless the user asked.

1. **Attach the pull request to the issue** — `save_issue` with `id` set to this
   issue's identifier and
   `links: [{url: "<the PR URL>", title: "<the PR title>"}]`. The `id` is what
   makes it an update: without it `save_issue` *creates* a new issue and the
   attachment lands nowhere. Do not skip this. Nothing else here links the two:
   Linear's GitHub integration links a pull request from
   the branch name, the title or the description, and none of those may carry a
   tracker key on this repository. A Linear comment containing the URL does not
   create an attachment either.
2. **Post a comment on the issue with the PR URL** (`save_comment`), so a person
   reading the issue can see where the work went.

**Keep the tracker out of the pull request body.** This repository is public and
the tracker is not, so no issue key and no tracker URL belongs in PR text —
`AGENTS.md` §"Nothing from the internal tracker goes into PR text" is the rule,
and a tool that appends a `Linear Issue:` footer for you needs that footer turned
off (davitf, 2026-09-20). That footer used to be what made Linear link the pull
request, which is why step 1 now has to be done deliberately.

## 3. Hand the pull request to the review loop

Once the PR is up and the fix is on the remote, put it in the loop and stop.
Do not start this step on uncommitted work, and do not review the diff yourself.

**Add the `review` label when you are finished pushing.** Adding it is what
starts the review, and everything pushed before it is what gets read:

```bash
gh pr edit <number> --add-label review
```

That is the whole handoff. `code-review-skill` runs against the PR in a separate
Claude session, posts the review there, and closes the round with a comment that
says whether it wants to see the fixes.

Two consequences worth stating, because they change what this session does next:

- **Nothing here waits for the review.** It arrives on the pull request minutes
  later, on GitHub, not as a return value. Say in your reply that the loop has
  it, and leave.
- **Stop pushing once you have added the label.** A push after it may land after
  the review has read the branch, and then nothing reviews it until someone adds
  the label again.

If the loop is not available — no GitHub Actions, or a fork, where the workflow
has no secrets — say so and stop rather than reviewing your own work. A review
this session writes is not a second opinion whatever it is labelled.

## 4. Address the findings

The review lands on the pull request, followed by the round's closing comment.
Work through the findings with
[`address-review-findings`](../address-review-findings/SKILL.md) — ledger,
reproduce-before-fix, gates, one decision packet at a time, replies on the PR.
When the closing comment asks to see the fixes, add the `review` label again
after pushing them; that skill's §7 says when not to.

Only one agent works a branch. If someone else picked it up, leave it alone:
two agents pushing to one branch is worse than a slower round. Steward skips a
second round only when a disposition comment (opener names
`address-review-findings`) is already on those finding IDs. Two agents can still
start in the same minute before either replies; that race is accepted — do not
invent a label or marker to close it.

The review stops asking for rounds once it no longer needs to see the result,
or the moment it raises a question only the maintainer can answer, and an agent
gets five rounds at most. None of that is this session's to override.

## Never

- Review your own diff, or “quickly glance” instead of step 3.
- Spawn a reviewer subagent of your own. The loop is the reviewer; a second one
  costs credits to duplicate a review that is already coming.
- Keep pushing to the branch after the handoff “while waiting”. The review may
  have read the branch already, and nothing reviews those pushes.
- Implement from the title without reading Linear comments.
- Skip posting the PR URL on the Linear issue.
- Mark the Linear issue done, or close it, unless the user asked.
- Run `code-review-skill` in this session and then pretend a second agent did it.
