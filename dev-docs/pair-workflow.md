# Pair workflow (maintainer-facing loop)

Everyday loop for non-trivial work: **investigate together → decide thinly →
implement → separate-agent review → escalate only real decisions**. It replaces
“pass unread OpenSpec change packs and three-block essays between agents” as the
thing the maintainer is expected to read.

This is **posture**, not a delete of OpenSpec or the existing review skills. Those
remain agent/CI tools. **Your** reading surface is the living handbook (below),
thin briefs, and **decision packets**.

Product tie-breaker remains [`VISION.md`](../VISION.md). Coding gates remain
[`CONTRIBUTING.md`](../CONTRIBUTING.md).

---

## The loop

```text
1. Investigate together     pair agent: read, measure, prototype (no silent commits)
2. Grill → handbook notes   decisions land on format/topic pages (light bullets)
3. Thin brief               ½–1 page: goal, non-goals, handbook links, verify commands
4. Implement                same pair agent; update handbook/user docs when claims move
5. Review (other model)     full findings → PR; YOU get decision packets only
6. Address                  pair agent; one packet at a time until happy
7. User docs if needed      Diátaxis mode + unslop (published `docs/` only)
```

| Phase | Human sees | Agents may also use |
| --- | --- | --- |
| Investigate / grill | Conversation + handbook edits | code map, threat model, tests, old ADRs/investigations as sources |
| Brief | One short markdown brief (PR description or `dev-docs/` scratch) | Optional OpenSpec **minimalist** change if a living main-spec contract must move |
| Implement | Diff + handbook/user-doc updates in the same PR when claims change | Existing OpenSpec apply skills only when a change folder exists |
| Review | **Decision packets only** | Full three-block / inline review on the PR for the implementor agent |
| Address | Next packet, cold-start readable | `address-review-findings` dispositions on the PR |

**Manual second agent for review is intentional** (e.g. implement in Cursor, review in
Claude, or the reverse). Do not require multi-model “interrogate” by default.

---

## Living handbook (what you read instead of specs/ADR spam)

Organised, **no-fluff**, rewritten in place — not an append-only log.

| Path | Role |
| --- | --- |
| [`formats/`](formats/README.md) | Per-format: behaviour here, consequences, light decisions, verify |
| [`topics/`](topics/README.md) | Cross-cutting: detection, extraction safety, streaming/cost, errors |
| [`code-map.md`](code-map.md) | Where to start in the tree |
| [`threat-model.md`](threat-model.md) | Trust boundaries (edit in place) |
| [`investigations/`](investigations/) | Append-only evidence notebooks; link from handbook, don’t promote to “current truth” |
| [`decisions/`](decisions/) | **Rare** repo-wide policy only; prefer light notes on format/topic pages |
| `openspec/specs/` | Optional dense agent/CI contract — **not** the maintainer UI |

**Docs with code:** if a PR makes a handbook or published-doc **claim false**, update that
page in the **same PR**. Do not mint a new ADR or OpenSpec essay just to record the
change of mind.

**Pilot:** grow `formats/` one format at a time (7z or RAR first). Fold useful bits from
old ADRs / investigations into that page; leave the ADR file as historical pointer or
archive later.

---

## Thin brief (replaces “read the change pack”)

Enough for a cold agent or a cold you:

1. **Goal** / **non-goals**
2. Links to handbook sections (or ADRs if still the only record)
3. Public contract deltas (only if user-visible / main-spec behaviour moves)
4. **Verify** — commands/tests that prove it
5. Out of scope

Prefer this as the PR body. Use `openspec new change … --schema minimalist` only when
main specs must change; keep scenario farms out of what you are asked to read.

---

## Decision packet (the only escalate form)

One question per turn. Decidable without opening the PR. Used by address-review and by
review block 3.

1. **Question** — one sentence, plain language  
2. **Why it matters** — user / API / security consequence  
3. **Options** — 2–3, each with cost/risk  
4. **Evidence** — what was run or read (command, snippet, failing test)  
5. **Recommendation** — labelled  
6. **Default if you ignore this** — what ships  

If an agent cannot fill these, it is not ready to ask — it should measure first.

Review quality does **not** drop: the implementor still gets the full finding list on the
PR. The maintainer is not the audience for that list unless they ask.

---

## How to start using this (checklist)

1. **Read this file** and the [`formats/`](formats/README.md) / [`topics/`](topics/README.md) stubs.  
2. **Invoke the thin skills** below on the next medium change (`/grill-with-handbook`,
   then implement against a thin brief).  
3. **Review with the other model** via `/code-review` / `/code-review-skill`; tell it
   explicitly: *post the full handoff on the PR; message me only with decision packets.*  
4. **Address** with `/address-review` — expect packets, not a wall of finding recap.  
5. **Pilot one format page** the next time you touch 7z or RAR; migrate light decisions
   onto that page as you go.  
6. **Optional IDE plugins** (desktop only — not Cloud Agents): `/add-plugin pstack` for
   extra lenses; do **not** vendor the whole plugin into the repo. Matt’s pack: install
   selectively or rely on the archivey-adapted skills here.

Until a format page exists, point the brief at `code-map`, threat model, and the best
existing ADR/investigation — then write the missing handbook section as part of the work.

---

## What to take from external stacks (docs vs skills)

Do **not** copy either full tree into `.claude/skills/`. Prefer **archivey-owned thin
skills** (already adapted below) plus this handbook. Use upstream plugins only as
optional desktop extras.

| Need | Source | In this repo | Why this shape |
| --- | --- | --- | --- |
| Sharpen plan + write decisions | Matt `grill-me` / `grill-with-docs` / `domain-modeling` | **Skill** [`grill-with-handbook`](../.claude/skills/grill-with-handbook/SKILL.md) | Writes format/topic pages, not ADR spam |
| Diátaxis + sentence craft | pstack `technical-writing` | **Skill** [`technical-writing`](../.claude/skills/technical-writing/SKILL.md) (condensed) | User `docs/` + PR/handbook prose |
| Strip AI tells | pstack `unslop` | Folded into `technical-writing` | One invoke for published prose |
| Agent-facing doc craft | Matt `writing-for-agents` | **Doc section** below (not a second skill yet) | Use when editing `AGENTS.md` / skills |
| Deep-module vocabulary | Matt `codebase-design` | Optional later **doc** under `topics/` | Borrow terms; don’t import TS tooling |
| Multi-model adversarial review | pstack `interrogate` | **Not default** | Manual other-model review is enough; escalate only for parsers/safety |
| Standards × Spec review split | Matt `code-review` | **Lens** inside existing review addendum | Spec axis = brief + handbook (+ main specs if touched) |
| prove-it / subtract-before-add / reader-load | pstack principles | **Doc bullets** in “Quality lenses” below | Don’t run full poteto-mode |
| OpenSpec explore/propose/apply | already vendored | Keep for contract moves / legacy changes | Not the maintainer reading surface |
| code-review / address-review / steward | already vendored | Keep; decision-packet rules tightened | Review depth stays; human UI shrinks |

### Quality lenses (borrow, don’t skill-ify)

When implementing or reviewing, prefer:

- **Prove it** — run the real test/command; don’t assert from prose alone.  
- **Subtract before add** — delete or shrink before new abstraction.  
- **Minimize reader load** — fewer layers and less hidden state for the next reader.  
- **Blast radius** — for risky format/detection/extraction diffs, name what else could
  break and pin it with a test when cheap.

### Writing for agents (crib from Matt; apply by hand)

When editing `AGENTS.md` or a `SKILL.md`:

- Put triggers in the description; keep the body progressive (steps first, reference later).  
- Prefer one source of truth; link the handbook instead of restating format lore.  
- Completion criteria must be checkable (“handbook section X updated”, “tests Y green”).

---

## Relationship to OpenSpec and deep `review/`

| Still use | When |
| --- | --- |
| `openspec/specs/` main specs | Contract must stay machine-checkable; agents implementing against SHALL/scenarios |
| OpenSpec **minimalist** change | Small contract delta; you still don’t want a four-file novel |
| OpenSpec **library** change | Rare large capability redesign — treat proposal/design as agent bus; put human conclusions on handbook pages |
| `review/` deep-review program | Commissioned thematic passes — unchanged |
| Everyday pair loop (this doc) | Default for feature/bug/refactor work you drive |

If handbook and main specs disagree, **pause and ask** (same rule as today) — that usually
means a decision was never recorded on the handbook page.

---

## Cursor / Claude entry points

| You want | Invoke |
| --- | --- |
| Pair investigation + decisions on handbook | `/grill-with-handbook` (after or during explore) |
| Explore without implementing | `/openspec-explore` stance is fine; don’t open a verbose change by default |
| User-facing or handbook prose craft | `/technical-writing` |
| Review (other agent) | `/code-review` or `/code-review-skill` — full PR handoff; packets to maintainer |
| Address review | `/address-review` — dispositions on PR; packets to you |
| PR babysitting | `steward` as today |

Desktop-only: `/add-plugin pstack` then `/setup-pstack` if you want poteto playbooks as an
extra; Cloud Agents will not see that plugin unless skills are vendored (we are not
vendoring it).
