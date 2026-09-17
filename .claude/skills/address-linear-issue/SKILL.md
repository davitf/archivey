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

Once the PR is up and the fix is on the remote, launch a **new** Task subagent.
Do not start this step on uncommitted work.

| Parameter | Value |
|---|---|
| `subagent_type` | `generalPurpose` |
| `model` | `cursor-grok-4.6-medium` (Cursor Grok, **standard**) |
| `resume` | omit — never resume an existing agent |
| `run_in_background` | `false` — wait for the review to post |

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

The reviewer does not implement. Point it at the skill and the PR; give it
everything it needs because it has none of this session's context:

```
You are a fresh reviewer. Edit nothing.

1. Read `.claude/skills/code-review-skill/SKILL.md` and
   `.claude/skills/code-review-skill/reference/archivey-review-addendum.md`
   (especially §0 output shape and §10 posting). A bare `/code-review` is a
   host builtin — do not use it.
2. Review PR <url> (branch <name>, HEAD <sha>) against main.
   Linear issue <id>: <title>. <one-line summary of the intended change>.
3. Post findings to that PR per addendum §10: stable IDs (F1, F2, …);
   located findings as inline comments; blocks 1 and 3 in the review body;
   name `address-review-findings` as the responder skill. Host posting tool
   in Cursor: ManagePullRequest (`post_comment`). Cursor does not append an
   attribution footer — add one. Open every comment with:
   **Cursor Grok** · `code-review-skill` · review of `<sha>`
4. Return the PR URL and the finding IDs you posted. Do not push, commit, or
   edit the tree.
```

If the Task call fails because of the model slug, retry with another non-fast
`cursor-grok-4.6-*` slug. Do not retry with a fast slug. Do not review the diff
yourself to unblock.

## 4. Address the findings

When the reviewer returns, **this** session (the implementer) reads
[`address-review-findings`](../address-review-findings/SKILL.md) and follows it
— ledger, reproduce-before-fix, gates, one decision packet at a time, replies
on the PR.

Do not spawn a second reviewer unasked. Stop after the dispositions land.

## Never

- Review your own diff, or “quickly glance” instead of step 3.
- Use a `*-fast` model for the reviewer, `inherit`, or `resume`.
- Skip Linear comments, or implement from the title alone.
- Mark the Linear issue done, or close it, unless the user asked.
- Run `code-review-skill` in this session and then pretend a second agent did it.
