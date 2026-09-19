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

Runs on Cursor desktop, Cursor Cloud Agent, and Claude Code. Other hosts that
can spawn a second agent follow the same spawn rules with that host’s tools.

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

## 3. Fresh reviewer (hard rules)

Once the PR is up and the fix is on the remote, launch a **new** subagent.
Do not start this step on uncommitted work.

### Spawn (this session)

Invariant: a *fresh* subagent, an explicit model (never omit, never `inherit`),
never a fast variant. Cursor Grok **standard** means a non-fast Cursor Grok
slug; the current one is `cursor-grok-4.6-medium`. If that slug is missing,
pick another Cursor Grok slug that does not end in `-fast`. If no Cursor Grok
slug is listed, pick a different model family than this session, from that
host’s allowed list, and say which. Do not retry a rejected slug with a fast one.

**Cursor** (desktop and Cloud Agent) — Task tool:

| Parameter | Value |
|---|---|
| `subagent_type` | `generalPurpose` |
| `model` | `cursor-grok-4.6-medium` |
| `resume` | omit — never resume an existing agent |
| `run_in_background` | `false` — wait for the review to be written |

**Claude Code** — `Agent` tool, `subagent_type: general-purpose` (hyphenated).
Pass an explicit `model` from that host’s allowed list. Never inherit.

If neither tool exists, use that host’s equivalent of a new session. If you
cannot spawn a second agent at all, stop and say so. Do not review the diff
yourself.

### Post

The reviewer posts per addendum §10. Try in this order:

1. Cursor `ManagePullRequest` (`post_comment`).
2. Linear `save_diff_comment` (inline) and `submit_diff_review` (body), which
   sync to GitHub.
3. Return the three-block markdown **with no attribution footer**. This session
   posts the reviewer’s words and applies **this host’s** §10 footer rule.
   Posting the reviewer’s words is not self-review.

A missing post tool is not a reason to skip the review or to write a substitute.

The opener is the reviewer’s — it survives a relay and names who wrote the
review: **{reviewer name}** · `code-review-skill` · review of `<sha>`. The
footer is the **poster’s** job, not the reviewer’s. Relayed markdown that
already contains a footer must have that footer stripped before this session
posts, then the poster’s rule applied.

### Prompt

The reviewer does not implement. Fill in the placeholders. `{reviewer name}` is
`Cursor Grok` only for a non-fast Cursor Grok slug; otherwise name the model
that actually ran.

```
You are a fresh reviewer. Edit nothing.

1. Read `.claude/skills/code-review-skill/SKILL.md` and
   `.claude/skills/code-review-skill/reference/archivey-review-addendum.md`
   (especially §0 output shape and §10 posting). A bare `/code-review` is a
   host builtin — do not use it.
2. Review PR <url> (branch <name>, HEAD <sha>) against main — or, on a
   re-review, against the HEAD you last reviewed, per addendum §10.
   Linear issue <id>: <title>. <one-line summary of the intended change>.
   Previous rounds: <IDs, dispositions, HEADs and what each round measured —
   or "first review">.
3. Post per addendum §10. Keep prior finding IDs stable; number new findings
   from the next free ID. A re-review opens with the §10 status table over
   those IDs. End block 3 with exactly:

   Addressing these: `.claude/skills/address-review-findings/SKILL.md`.

   Posting order: ManagePullRequest (`post_comment`); else Linear
   `save_diff_comment` + `submit_diff_review`; else return the three-block
   markdown with no attribution footer and do not invent a GitHub write.
4. Open every posted comment with:
   **{reviewer name}** · `code-review-skill` · review of `<sha>`
   If you post yourself, apply your host’s §10 footer rule. If you return
   markdown for the parent to post, include no footer.
5. Return the PR URL, the finding IDs you posted, and the three-block body
   (no footer) if posting failed. Do not push, commit, or edit the tree.
```

If the spawn call fails because of the model slug, retry with another non-fast
Cursor Grok slug (or another allowed non-fast model on a non-Cursor host). Do
not retry with a fast slug. Do not review the diff yourself to unblock a spawn
failure.

## 4. Address the findings

When the reviewer returns, **this** session (the implementer) reads
[`address-review-findings`](../address-review-findings/SKILL.md) and follows it
— ledger, reproduce-before-fix, gates, one decision packet at a time, replies
on the PR.

This session owns dispositions for the review it commissioned until it stops.
Do not wait for steward. Steward skips a second round only when a disposition
comment (opener names `address-review-findings`) is already on those finding IDs.
Two agents can still start in the same minute before either replies; that race
is accepted — do not invent a label or marker to close it.

Stop after the dispositions land. A 🔄 verdict does not spawn a second
reviewer; the user asks for the next round. Fill the prompt’s “Previous
rounds” placeholder from the ledger when that happens.

## Never

- Review your own diff, or “quickly glance” instead of step 3.
- Use a fast model for the reviewer, `inherit`, or `resume`.
- Implement from the title without reading Linear comments.
- Skip posting the PR URL on the Linear issue.
- Mark the Linear issue done, or close it, unless the user asked.
- Run `code-review-skill` in this session and then pretend a second agent did it.
