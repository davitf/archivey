# Code review

Review the relevant code with a code review mindset.

## Priorities (Cursor `/code-review` defaults)

Keep these as the primary lens:

1. **Bugs** and correctness errors
2. **Behavioral regressions**
3. **Security / safety** issues
4. **Missing tests** (especially red–green for bug fixes)

**Findings must be the primary focus**, ordered by severity. Do **not** make code
changes unless the user explicitly asks for them.

## Archivey process (best of both)

This repo vendors a fuller review skill under `.claude/skills/code-review-skill/`.
Combine the priorities above with that skill’s process:

1. **Read process rules:** `.claude/skills/code-review-skill/reference/archivey-review-addendum.md`
   — especially **§0 (output shape + finding discipline)** and **§8 (code first, then
   context)**, plus VISION ranking, contracts, and the domain checklist. Do **not**
   absorb OpenSpec / design / long PR rationale before the cold code pass.
2. **Pass 1 — code alone:** changed code (+ nearby context) for self-explanatory
   sense in the resulting tree, local docs for non-obvious choices, bugs/safety/tests.
   Use `.claude/skills/code-review-skill/SKILL.md` — logistics, per-area pass-1 list,
   severity labels; open deeper guides under `reference/` only as needed.
3. **Pass 2 — context (required):** PR narrative / thin brief, living handbook pages
   under `dev-docs/formats/` and `dev-docs/topics/` when present, OpenSpec **main**
   specs only if this change touches that contract, VISION / threat model / addendum
   rows — check contract fit; pause-and-ask on discrepancies. Findings that only
   dissolve after external prose are usually documentation debt in the code
   (addendum §8). Prefer the handbook + brief as the Spec axis over unread change-folder
   novels (`dev-docs/pair-workflow.md`).

**Split audience:** post the full three-block review on the PR for the implementor. If
you are also talking to the maintainer, send **decision packets only** (addendum §0
block 3) unless they ask for the full handoff. Do **not** write a prompt or brief for the
implementing agent on top of that — it reads the PR threads itself (addendum §0).

## Scope

- Default: current branch vs `main` (`git diff main...HEAD` and/or `@Branch`), plus any
  paths or PR the user named.
- If the user is asking about uncommitted work, include the working-tree diff.
- Prefer concrete `file:line` evidence and triggering inputs/states.
- **Reviewing an OpenSpec proposal / `design.md` instead of code?** Skip the code-first
  order and use the addendum's **§9 (values-first)** — check the design against
  VISION/CONTRIBUTING values and contracts, then proposal shape, then hunt **decision
  gaps & unknown unknowns** (what an implementor must decide first; what the proposal
  isn't thinking about) and raise them as maintainer questions.

## Output format

Emit **exactly three sections** in the addendum's **§0** shape — maintainer briefing,
implementor handoff (written to go on the PR), maintainer decisions — with §0's brevity
fence, two-axis
labelling (severity × confidence) and reclassification rules. Read §0 rather than working
from a summary here; `assets/pr-review-template.md` is the fill-in form.

Two things worth repeating at the point of writing:

- The maintainer often has not read the diff. Do not dump a long findings list first.
- Rank archivey blockers with the addendum (VISION claims, exception contract, path/bomb
  safety, silent solid re-decode, unjustified debt) — §7 has the mapping.

Skip formatting/lint nits that `ruff` / the type-checkers already own.

## Posting to a PR

If the review is being posted to a pull request rather than printed here, follow addendum
**§10**: give every block-2 finding a **stable ID** (`F1`, `F2`, … — kept across
re-reviews), post located findings as **inline comments** so they can be replied to and
resolved individually, put blocks 1 and 3 in the review body, open a re-review with a
**status table over the previous IDs**, and make every comment identifiable as
agent-authored — a distinct bot identity if your host posts under one, otherwise an
attribution footer naming the tool that wrote it. Do **not** re-run the gates unless
you are actually testing a claim (addendum §10).

The implementing agent then works through it with `/address-review`
([`.claude/skills/address-review-findings/`](../../.claude/skills/address-review-findings/SKILL.md)).
