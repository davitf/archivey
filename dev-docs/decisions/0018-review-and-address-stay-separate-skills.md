# 0018 — Review and address stay two skills; auto-watchers route through `steward`

- **Status:** accepted
- **Date:** 2026-08-20
- **Provenance:** `AGENTS.md` §"Review workflow (two agents, two skills)";
  `.claude/skills/code-review-skill/reference/archivey-review-addendum.md` §10;
  `.claude/skills/address-review-findings/SKILL.md`

## Context

PR review here is a handoff between two agents with a skill each: `code-review-skill`
produces findings and posts them; `address-review-findings` gives every finding an
explicit disposition. That handoff assumes the implementing agent **invokes the second
skill**.

Increasingly it does not. A session subscribed to PR activity is woken by a review
comment or a CI failure as an *event*, and acts from its own generic posture. It never
invokes a skill nobody named. So the discipline that makes this repo's review loop work —
build the ledger before editing, reproduce before fixing, run both gate scripts, escalate
one question at a time, resolve the threads you resolved, sign the comment — reaches
exactly the agents that were told to use it, and none of the ones that show up on their
own. Three options were considered.

1. **Merge the two skills into one, with a review mode and a fix mode**, the way the
   builtin `/code-review` has `--comment` and `--fix`. Rejected, for three reasons. The
   addendum already forbids the behaviour it invites — §10 "Do not fix while reviewing:
   leaving the fix to the implementing agent is what keeps the review a second opinion
   rather than a self-graded one." Triggering degrades: a skill's description is what
   loads it, and a merged description pulls ~500 lines of review rubric plus eight
   reference guides into a session that needs only the disposition half, and the reverse.
   And the modes would not fit the shape of the work: `--fix` works because it is one
   agent, one session, its own diff, whereas the two halves here run in different
   sessions and often different hosts (Cursor `/code-review` vs Claude Code). It also
   would not have solved the problem — a watcher that invokes no skill invokes no *mode*
   either.
2. **Put the instruction in the posted review**, a line in block 3 naming the responder
   skill. Nearly free, and it reaches readers nothing else does: a human, a Cursor
   session, a later round by a different bot. But it is advisory prose inside a comment,
   read by an agent already running its own posture. Good as a second layer, not as the
   mechanism.
3. **A repo-side file the watcher already reads.** A Claude Code session subscribed to PR
   activity is told — in the operating instructions it starts the session with — to read
   `.claude/skills/steward/SKILL.md` (or `babysit/`) from the head branch *before* acting
   on a CI or review event, and to let it take precedence on conventions and on how
   proactive to be. This repo had neither file. See "Where the filename comes from" below
   for what that instruction is and is not.

## Decision

**Keep `code-review-skill` and `address-review-findings` separate, and add
`.claude/skills/steward/SKILL.md` as a router for agents that arrive via PR events.**

- `steward` carries **no process of its own**. It points at `address-review-findings` and
  records only the deltas from a watcher's built-in defaults: reproduce before fixing;
  the gate is `./scripts/check.sh --fix` *and* `./scripts/test.sh`, because pushing after
  `ruff` alone is this repo's most common self-inflicted CI failure; escalate with
  `AskUserQuestion` one question at a time rather than proposing on the thread; resolve
  the threads you resolved; a disproven finding is usually doc-debt; every comment gets an
  agent attribution footer.
- **Small fixes are autonomous.** A watcher pushes without asking when the fix is confined
  to code the PR touches, implies no change to `openspec/specs/`, the threat model, a
  public API shape or `docs/`, was reproduced first (red–green for bugs), and passes the
  full gate. Contract and product calls, conflicts between authoritative sources, scope
  rulings, and substantive disagreement with a human reviewer still stop and ask.
- Option 2 is adopted **as well**: addendum §10 now requires the review body to name the
  responder skill, which covers hosts that never read `steward`.

### Where the filename comes from

Worth stating plainly, because it is load-bearing and easy to mistake for a documented
extension point. The instruction to read `.claude/skills/steward/SKILL.md` before acting
on a PR event arrives in the **operating instructions a Claude Code session receives when
it is subscribed to PR activity**. It is a harness convention, not a published API: the
public Claude Code documentation describes skill loading by description matching and
`CLAUDE.md` auto-load, and does not document this filename. Two consequences follow.

- **Do not rename the directory.** Nothing here references it through a mechanism that
  would fail loudly. A rename would silently return watchers to their generic posture, and
  the repo would look correctly configured while the file was read by nobody. `babysit/`
  is the alternative name the same instruction names; it is deliberately *not* also added,
  since `steward/` takes precedence where both exist.
- **Treat it as revocable.** A convention delivered through a system prompt can change
  without a deprecation notice. The review-body pointer (addendum §10) does not depend on
  it, which is a second reason to keep both rather than picking one.

## Consequences

- **The rules live in one place.** `steward` restating `address-review-findings` would
  have created a second copy to drift; a router cannot drift out of sync with what it
  points at.
- **The review stays a second opinion.** No mode exists in which the agent that found a
  problem is also the one that judges its own fix.
- **The autonomy boundary is written down**, so it is a repo decision rather than
  whichever posture the watching session happened to start from. The gap it closes is real
  but narrower than "two documents disagree": a watcher's default is push-first, and
  `address-review-findings` §6 says *when* to escalate without ruling on what an agent may
  fix unasked — it explicitly tells you **not** to escalate routine implementation choices.
  Neither text answers the question directly, so an unguided agent generalises from
  whichever it read last, and every finding becomes either an escalation or a push.
- **Coverage is still partial, deliberately.** `steward` is a Claude Code convention;
  `cursor[bot]`, `qodo-code-review[bot]` and any future GitHub-Action reviewer do not read
  it. Those get the review-body pointer and nothing stronger. Accepted: the alternative is
  duplicating the rules into every host's config, which is the drift this decision avoids.
- **A third file joins the review loop.** `AGENTS.md` §"Review workflow" and `CLAUDE.md`
  both name it, so the entry point is discoverable from the two files an agent reads first.
