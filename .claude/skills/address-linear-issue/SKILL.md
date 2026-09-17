---
name: address-linear-issue
description: |
  Read a Linear issue, implement the fix, then hand the PR to a fresh Cursor Grok
  (standard, never fast) subagent running code-review-skill. The implementing
  agent then dispositions the review with address-review-findings.
  Use when: addressing a Linear issue, implementing a Linear ticket, "fix LIN-123",
  working from a Linear URL, or when the user invokes /address-linear-issue.
---

# Address a Linear issue

Orchestrator only. Review process stays in
[`code-review-skill`](../code-review-skill/SKILL.md) (addendum §10 for posting);
dispositions stay in [`address-review-findings`](../address-review-findings/SKILL.md).
Do not restate those files here.

The review **must** be a second opinion: a *fresh* subagent, not this session
grading its own diff (ADR
[0018](../../../dev-docs/decisions/0018-review-and-address-stay-separate-skills.md)).

Runs on Cursor desktop, Cursor Cloud Agent, and other hosts that can spawn a
second agent. It is not desktop-only.

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

Push a branch and open a PR. If the issue already names a PR or branch, continue
there instead of opening a second one.

After the PR exists, post a Linear comment on the issue with the PR URL
(`save_comment`). Do not change Linear status unless the user asked.

Gates before you consider the fix ready to review:

```bash
./scripts/check.sh --fix
./scripts/test.sh
```

`--all-configs` when extras or versions matter (`CONTRIBUTING.md` §"Before pushing…").

## 3. Fresh reviewer (hard rules)

Once the PR is up and the fix is on the remote, launch a **new** subagent.
Do not start this step on uncommitted work.

### Spawn (this session)

In Cursor (desktop and Cloud Agent) the parent uses the Task tool:

| Parameter | Value |
|---|---|
| `subagent_type` | `generalPurpose` |
| `model` | `cursor-grok-4.6-medium` (Cursor Grok, **standard**) |
| `resume` | omit — never resume an existing agent |
| `run_in_background` | `false` — wait for the review to be written |

**Never pass a slug ending in `-fast`.**
`cursor-grok-4.6-medium-fast`, `cursor-grok-4.6-high-fast`, and the other
`*-fast` slugs are forbidden, including as a fallback when the requested slug
is rejected.

**Never omit `model` and never pass `inherit`.** Omitting inherits the parent,
which may be a fast variant. If `cursor-grok-4.6-medium` is not in the allowed
list, pick another `cursor-grok-4.6-*` slug that does **not** end in `-fast`.
If no Cursor Grok slug is listed (non-Cursor host), still spawn a *fresh*
subagent on a different model than the implementer so the review is a second
opinion, and say which slug you used.

If Task is missing, use that host’s equivalent of a new session. If you cannot
spawn a second agent at all, stop and say so. Do not review the diff yourself.

### Post (the reviewer)

The reviewer posts the addendum §0 three-block review (blocks 1, 2, and 3) to
the PR. Located findings also go inline. Try posting in this order:

1. Cursor `ManagePullRequest` (`post_comment`) — inline for `file:line` findings,
   top-level for the three-block body.
2. Linear `save_diff_comment` (inline) and `submit_diff_review` (body), which
   sync to GitHub.
3. Return the full three-block markdown to this session. **This session posts
   that text unchanged.** Posting the reviewer’s words is not self-review.

A missing post tool is not a reason to skip the review or to write a substitute.

### Prompt

The reviewer does not implement. Fill in the placeholders; it has none of this
session’s context. `{reviewer name}` is `Cursor Grok` only when the model slug
starts with `cursor-grok-4.6-` and does not end in `-fast`; otherwise name the
model that actually ran.

```
You are a fresh reviewer. Edit nothing.

1. Read `.claude/skills/code-review-skill/SKILL.md` and
   `.claude/skills/code-review-skill/reference/archivey-review-addendum.md`
   (especially §0 output shape and §10 posting). A bare `/code-review` is a
   host builtin — do not use it.
2. Review PR <url> (branch <name>, HEAD <sha>) against main.
   Linear issue <id>: <title>. <one-line summary of the intended change>.
3. Post the addendum §0 three-block review to that PR (blocks 1, 2, and 3 in
   the top-level body; located findings also inline) with stable IDs
   (F1, F2, …). End block 3 with exactly:

   Addressing these: `.claude/skills/address-review-findings/SKILL.md`.

   Posting order: ManagePullRequest (`post_comment`); else Linear
   `save_diff_comment` + `submit_diff_review`; else return the three-block
   markdown to the parent and do not invent a GitHub write.
4. Open every posted comment with:
   **{reviewer name}** · `code-review-skill` · review of `<sha>`
   If the host does not append an attribution footer (Cursor does not), end
   with:

   ---
   _Generated by {reviewer name} (`code-review-skill`)_

   Claude Code appends its own footer — do not add a second one there.
5. Return the PR URL, the finding IDs you posted, and the three-block body if
   posting failed. Do not push, commit, or edit the tree.
```

If the Task call fails because of the model slug, retry with another non-fast
`cursor-grok-4.6-*` slug. Do not retry with a fast slug. Do not review the diff
yourself to unblock a spawn failure.

## 4. Address the findings

When the reviewer returns, **this** session (the implementer) reads
[`address-review-findings`](../address-review-findings/SKILL.md) and follows it
— ledger, reproduce-before-fix, gates, one decision packet at a time, replies
on the PR.

This session owns dispositions for the review it commissioned until it stops.
A `steward` wake on the same PR during that window must no-op — do not start a
parallel `address-review-findings` round on the same F-IDs.

Stop after the dispositions land. A 🔄 verdict does not spawn a second
reviewer; the user asks for the next round.

## Never

- Review your own diff, or “quickly glance” instead of step 3.
- Use a `*-fast` model for the reviewer, `inherit`, or `resume`.
- Implement from the title without reading Linear comments.
- Skip posting the PR URL on the Linear issue.
- Mark the Linear issue done, or close it, unless the user asked.
- Run `code-review-skill` in this session and then pretend a second agent did it.
