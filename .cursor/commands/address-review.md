# Address review findings

Work through the review feedback on this PR and give **every** finding an explicit
disposition. The full process lives in the skill:
[`.claude/skills/address-review-findings/SKILL.md`](../../.claude/skills/address-review-findings/SKILL.md)
— read it first. This file is the Cursor entrypoint, not a second copy of the rules.

## The short version

1. **Gather all four channels** — top-level comments, inline review threads, formal
   reviews, and CI check failures. Reading only one is how half a review gets missed.
   Note who actually wrote each comment: unless the host has its own bot identity, agents
   post through the maintainer's account, so a `davitf` comment *without* an agent
   attribution footer is the human.
2. **Ledger first, edits second.** Every finding gets an ID and exactly one disposition:
   fix / disproven / escalate / defer-with-a-written-home. Nothing is dropped silently.
3. **Verify before acting.** Reproduce the finding. Automated reviewers here do produce
   false positives, and real findings are often narrower and sharper than described.
4. **Fix the cause, red–green, specs and docs move with the contract.** If an ad-hoc
   audit found it, convert the audit into a standing test.
5. **Full gate before pushing** — `ruff`, **`pyrefly` and `ty`**, `pytest`, then the three
   dependency configs when extras or versions matter. Check the cross-platform traps
   (AGENTS.md) since CI runs Windows and macOS and you are on Linux.
6. **Escalate one decision packet at a time** (`AskUserQuestion`) — Question, why it
   matters, options, evidence, recommendation, default-if-ignored. The maintainer must
   be able to answer cold without reading the PR. Do **not** dump the full finding list
   into chat; that stays on the PR. Shape: `dev-docs/pair-workflow.md` and skill §6.
7. **Reply in the thread the finding came from, and resolve what you resolved.** Report
   gates honestly. Attribution footer on every comment.

## Scope

- Default: the open PR for the current branch.
- Include CI failures on that PR — a red check is a finding.
- Do **not** rewrite unrelated code, and do not close a finding by deleting the test that
  caught it.
