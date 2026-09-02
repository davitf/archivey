# Pair workflow adoption (external stacks → archivey)

**Written for circulation while adopting the loop.** Once the loop is habit, mark this
RESOLVED and leave the body as historical. Living truth:
[`../pair-workflow.md`](../pair-workflow.md).

Dated 2026-09-02.

> **Status: open.** Adoption in progress on PR #280. Update this header when the pilot
> format page exists and the entry-point commands are muscle memory. Spec thinning
> direction (related): [`2026-09-specs-to-handbook-and-tests.md`](2026-09-specs-to-handbook-and-tests.md).

## How to start (checklist)

1. Read [`../pair-workflow.md`](../pair-workflow.md). Handbook trees are **not** stubbed
   empty — the first format/topic page is created with the change that needs it.  
2. On the next medium change: `/grill-with-handbook`, then implement against a thin brief.  
3. Review with the other model via Cursor `/code-review` (project command) or
   **`/code-review-skill`** elsewhere — *post the full handoff on the PR; message the
   maintainer only with decision packets.*  
4. Address with Cursor `/address-review` or the `address-review-findings` skill — packets,
   not a wall of finding recap.  
5. **Pilot:** next time you touch 7z or RAR, create `dev-docs/formats/7z.md` or
   `rar.md` (skeleton in pair-workflow) and migrate light decisions onto that page.  
6. Optional desktop: Cursor marketplace **pstack** plugin for extra lenses — do **not**
   vendor it into the repo. Prefer archivey-adapted skills for Matt-style grilling/writing.

Until a format page exists, point the brief at `code-map`, threat model, and the best
existing ADR/investigation — then write the missing handbook page as part of the work.

## What to take from external stacks (docs vs skills)

Do **not** copy either full tree into `.claude/skills/`. Prefer **archivey-owned thin
skills** plus the handbook. Use upstream plugins only as optional desktop extras.

| Need | Source | In this repo | Why this shape |
| --- | --- | --- | --- |
| Sharpen plan + write decisions | Matt `grill-me` / `grill-with-docs` / `domain-modeling` | **Skill** [`grill-with-handbook`](../../.claude/skills/grill-with-handbook/SKILL.md) | Writes format/topic pages, not ADR spam |
| Diátaxis + sentence craft | pstack `technical-writing` | **Skill** [`technical-writing`](../../.claude/skills/technical-writing/SKILL.md) | User `docs/` + PR/handbook prose |
| Strip AI tells | pstack `unslop` | Folded into `technical-writing` | One invoke for published prose |
| Agent-facing doc craft | Matt `writing-for-agents` | Apply by hand when editing `AGENTS.md` / skills | Triggers in description; progressive body; checkable done criteria |
| Deep-module vocabulary | Matt `codebase-design` | Optional later **doc** under `topics/` | Borrow terms; don’t import TS tooling |
| Multi-model adversarial review | pstack `interrogate` | **Not default** | Manual other-model review is enough |
| Standards × Spec review split | Matt `code-review` | **Lens** inside existing review addendum | Spec axis = brief + handbook (+ main specs if touched) |
| prove-it / subtract-before-add / reader-load | pstack principles | Use as judgement while implementing/reviewing | Don’t run full poteto-mode |
| OpenSpec explore/propose/apply | already vendored | Keep for contract moves | Not the maintainer reading surface |
| code-review / address-review / steward | already vendored | Keep; decision-packet rules tightened | Review depth stays; human UI shrinks |

### Quality lenses (borrow, don’t skill-ify)

- **Prove it** — run the real test/command; don’t assert from prose alone.  
- **Subtract before add** — delete or shrink before new abstraction.  
- **Minimize reader load** — fewer layers and less hidden state for the next reader.  
- **Blast radius** — for risky format/detection/extraction diffs, name what else could
  break and pin it with a test when cheap.
