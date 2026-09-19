# Code review

Review the relevant code with a code review mindset. The process lives in the skill:
[`.claude/skills/code-review-skill/SKILL.md`](../../.claude/skills/code-review-skill/SKILL.md)
and its
[archivey addendum](../../.claude/skills/code-review-skill/reference/archivey-review-addendum.md)
— read both first. This file is the Cursor entrypoint. It routes and binds the Cursor-specific
facts; it is not a second copy of the rules, and a rule change should never need an edit here.

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
  happens to them across rounds, is addendum §10.
- **Cursor posts under its own `cursor[bot]` identity**, so add your own attribution footer;
  §10 says why that matters and what the footer is for.

## Where each thing is decided

| What | Where |
|---|---|
| Review order, and why code comes before context | Addendum **§8** |
| What to look for in archivey code | `CONTRIBUTING.md` for the rules; addendum **§3–§5** for which ones PRs here break |
| The output shape, severity × confidence, verdicts, the round budget | Addendum **§0**; `assets/pr-review-template.md` is the fill-in form |
| Posting to a PR: IDs, inline threads, re-review scope, what not to re-run, what to record | Addendum **§10** |
| Reviewing a proposal or `design.md` instead of code | `reference/reviewing-proposals.md`, via addendum **§9** |

Read those rather than working from a summary. Sending findings to the maintainer in chat as
well? Addendum §0 says which block goes where, and why you do not write a separate prompt for
the implementing agent.

## Scope

- Default: current branch vs `main` (`git diff main...HEAD` and/or `@Branch`), plus any paths
  or PR the user named. Uncommitted work? Include the working-tree diff.
- **Re-reviewing?** The scope is narrower than the whole PR — addendum §10.
- Prefer concrete `file:line` evidence and triggering inputs or states.
- Skip formatting and lint nits that `ruff` and the type-checkers already own.

The implementing agent then works through it with `/address-review`
([`.claude/skills/address-review-findings/`](../../.claude/skills/address-review-findings/SKILL.md)).
