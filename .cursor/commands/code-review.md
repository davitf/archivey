# Code review

Review the relevant code with a code review mindset. The process lives in the skill:
[`.claude/skills/code-review-skill/SKILL.md`](../../.claude/skills/code-review-skill/SKILL.md)
— read it first, then the one doc its §1 names for what you are reviewing. This file is
the Cursor entrypoint. It routes and binds the Cursor-specific facts; it is not a second
copy of the rules, and a rule change should never need an edit here.

## Priorities (Cursor `/code-review` defaults)

Keep these as the primary lens, on top of the skill's process:

1. **Bugs** and correctness errors
2. **Behavioral regressions**
3. **Security / safety** issues
4. **Missing tests** (especially red–green for bug fixes)

**Findings must be the primary focus**, ordered by severity. Do **not** make code changes
unless the user explicitly asks for them.

## Cursor-specific facts

- **Your finding-ID prefix is `C`** — `C1`, `C2`, … The rule those IDs follow, and what
  happens to them across rounds, is `SKILL.md` §6.
- **Cursor posts under its own `cursor[bot]` identity**, so add your own attribution footer;
  `SKILL.md` §6 says why that matters and what the footer is for.

## Where each thing is decided

| What | Where |
|---|---|
| Review order, and why code comes before context | `reference/code-pr.md` |
| What to look for in archivey code | `CONTRIBUTING.md` for the rules; `reference/code-pr.md` for which ones PRs here break |
| The output shape, severity × confidence, verdicts, the round budget | `SKILL.md` **§2–§5**; `assets/pr-review-template.md` is the fill-in form |
| Posting to a PR: IDs, inline threads, what not to re-run, what to record | `SKILL.md` **§6**; re-review scope is `reference/fix-round.md` |
| Reviewing a proposal or `design.md` instead of code | `reference/reviewing-proposals.md` |

Read those rather than working from a summary. Sending findings to the maintainer in chat as
well? `SKILL.md` §3 says which block goes where, and why you do not write a separate prompt for
the implementing agent.

## Scope

- Default: current branch vs `main` (`git diff main...HEAD` and/or `@Branch`), plus any paths
  or PR the user named. Uncommitted work? Include the working-tree diff.
- **Re-reviewing?** The scope is narrower than the whole PR — `reference/fix-round.md`.
- Prefer concrete `file:line` evidence and triggering inputs or states.
- Skip formatting and lint nits that `ruff` and the type-checkers already own.

The implementing agent then works through it with `/address-review`
([`.claude/skills/address-review-findings/`](../../.claude/skills/address-review-findings/SKILL.md)).

## When you approve, hand it to Claude for a pass from zero

This matters when **you** are the reviewer and Claude implemented — the roles run both
ways round. Once your verdict is ✅ Approve and you have posted the review, add the
`review` label to the pull request (`gh pr edit <number> --add-label review`), then stop.

That starts a Claude round through the
[review loop](../../dev-docs/review-loop.md). It reads the whole diff rather than a
fix-diff, because Claude has not reviewed this pull request — a second reviewer's first
look is not a re-review (`reference/fix-round.md`). A pass from zero after an approval is the point:
two reviewers who have read the same tree cold are worth more than one that read it
twice.

Two things to get right:

- **Last action, after everything else is posted.** The round reviews the pull request as
  it stands when it starts.
- **Only on approve.** A verdict of 🔄 Request Changes goes back to the implementer;
  there is nothing for a second reviewer to confirm yet.

If Claude's pass finds something real, that is the ordinary loop and not an escalation:
the implementer fixes it, and rounds continue under the same cap
(maintainer decision, davitf, 2026-09-19). An approval that a second reviewer disagrees
with is not evidence of anything, and stopping for a human would cost a round trip for
what is usually a nit.
