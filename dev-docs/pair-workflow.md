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

Adoption notes and the external-stack crib (what we borrowed from pstack / Matt)
live in
[`discussions/2026-09-pair-workflow-adoption.md`](discussions/2026-09-pair-workflow-adoption.md)
— one-time setup, not living truth.

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

## Living handbook (what you read instead of change packs / ADR spam)

Organised, **no-fluff**, rewritten in place — not an append-only log.

| Path | Role |
| --- | --- |
| `formats/<format>.md` | Per-format: behaviour here, consequences, light decisions, verify — **create with the first real change** that needs it (do not land empty trees) |
| `topics/<topic>.md` | Cross-cutting notes; **link** registers like [`threat-model.md`](threat-model.md), do not restate them — same “create on first use” rule |
| [`code-map.md`](code-map.md) | Where to start in the tree |
| [`threat-model.md`](threat-model.md) | Trust boundaries + open `O*` gap register (sole home for those IDs) |
| [`investigations/`](investigations/) | Append-only evidence notebooks; link from handbook, don’t promote to “current truth” |
| [`decisions/`](decisions/) | **Rare** repo-wide policy only; prefer light notes on format/topic pages once those exist |
| `openspec/specs/` | **Authoritative** machine-checkable contract (agents/CI) — **not** the primary human reading surface |

**Until the first format/topic page exists:** point briefs at `code-map`, threat model,
and the best ADR/investigation. When a change needs a living page, create
`dev-docs/formats/<format>.md` or `dev-docs/topics/<topic>.md` **in that same PR**, with:

1. Role here — support / refusals  
2. How it works in this repo — entrypoints (link code-map), solid/seek, codecs  
3. Consequences — perf, memory, crypto, bomb edges  
4. Decisions (light) — choice → why → rejected alternative  
5. Open pitfalls  
6. Verify — tests/commands that pin claims  

Optional `formats/README.md` / `topics/README.md` indexes may appear alongside the first
page; do not add empty stubs ahead of content.

**Docs with code:** if a PR makes a handbook or published-doc **claim false**, update that
page in the **same PR**. Do not mint a new ADR or OpenSpec essay just to record the
change of mind.

**Deferred design (not this page):** eventually thin OpenSpec scenarios into tests and
keep handbook principles + test links as the dual contract — see future discussion after
the first format pilot.

---

## Thin brief (replaces “read the change pack”)

Enough for a cold agent or a cold you:

1. **Goal** / **non-goals**
2. Links to handbook sections (or ADRs if still the only record)
3. Public contract deltas (only if user-visible / main-spec behaviour moves)
4. **Verify** — commands/tests that prove it
5. Out of scope

Prefer this as the PR body. Use `openspec new change … --schema minimalist` when main
specs must change; keep scenario farms out of what you are asked to read.

---

## Decision packet (canonical escalate form)

**This section is the single source of truth** for the packet shape. Review and
address skills point here; do not restate the six fields elsewhere.

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

## Relationship to OpenSpec and deep `review/`

| Still use | When |
| --- | --- |
| `openspec/specs/` main specs | Authoritative contract stays machine-checkable; agents implement against SHALL/scenarios. Not what you read day-to-day |
| OpenSpec **minimalist** change | Small contract delta; you still don’t want a four-file novel |
| OpenSpec **library** change | Rare large capability redesign — treat proposal/design as agent bus; put human conclusions on handbook pages |
| `review/` deep-review program | Commissioned thematic passes — unchanged |
| Everyday pair loop (this doc) | Default for feature/bug/refactor work you drive |

If handbook and main specs disagree, **pause and ask** (same rule as today) — that usually
means a decision was never recorded on the handbook page.

---

## Entry points

| You want | Invoke |
| --- | --- |
| Pair investigation + decisions on handbook | `/grill-with-handbook` |
| Explore without implementing | `/openspec-explore` stance; don’t open a verbose change by default |
| User-facing or handbook prose craft | `/technical-writing` (includes unslop) |
| Review (other agent) | Cursor: `/code-review` (project command → archivey skill). Elsewhere: **`/code-review-skill`** — never bare `/code-review` (that is a host builtin). Full PR handoff; packets to maintainer |
| Address review | Cursor: `/address-review`. Elsewhere: **`address-review-findings`** / ask for that skill by name |
| PR babysitting | `steward` as today |

Desktop-only extras (not Cloud Agents): install the Cursor **pstack** plugin from the
marketplace if you want poteto playbooks; do not vendor it into this repo. Matt’s pack:
prefer the archivey-adapted skills above over installing the full tree.
