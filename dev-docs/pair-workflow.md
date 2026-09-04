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

**Create a page when a change needs one**, not before: `dev-docs/formats/<format>.md` or
`dev-docs/topics/<topic>.md`, **in that same PR**. For a format or topic that has no page
yet, point briefs at `code-map`, the threat model, and the best ADR or investigation. The
shape to start from is below; [`formats/zip.md`](formats/zip.md) and
[`topics/prefixed-archives.md`](topics/prefixed-archives.md) are the worked examples.

### Format page structure

Settled by writing [`formats/zip.md`](formats/zip.md) first and taking the shape the
material actually had. Sections are numbered so a brief can cite `zip.md` §2.3.

| Section | Holds |
| --- | --- |
| **At a glance** | Support, costs, dependencies, refusals. Distinguishes *claimed* from *shipped* — a spec requirement for something that does not exist says so here |
| **1. Shape** | The two to four structural properties that generate everything else, each with its consequences attached in the same breath. Not a spec reproduction; the altitude specs skip |
| **2. The pipeline here** | Fixed subsections — identify · open and list · member data · extract · write. Each says *who does the work*, *what is format-specific rather than general*, and *what is refused*. "Nothing here is format-specific" is a legitimate and useful answer. Member-metadata mapping lives under *open and list*. A stage that hands work to a **separate process** answers a fourth question — *what crosses the boundary*, in both directions — because none of the first three reach it: [`formats/rar.md`](formats/rar.md) §2.3 is argv construction one way and an exit code plus a byte count the other, and that is the page |
| **3. In the wild** | Variants, producers and what they get wrong, files that are secretly this format, corpus evidence with its provenance |
| **4. Threat surface** | Format-specific attack surface only; link [`threat-model.md`](threat-model.md) `O*` rows for status |
| **5. Sharp edges** | *Symptoms someone observes*, each tagged **format** (inherent) / **library** (upstream or replace the library) / **archivey** (ours), so a reader can stop thinking about what they cannot fix. Details and fix plans stay behind the register link. **One table, not two**: a reader arrives with a symptom and does not yet know whether it is a bug or the format, so the tag sorts each row after they have found it rather than making them pick the right list first |
| **6. Decisions** | Choice → why → rejected alternative. Light bullets, not ADRs |
| **7. Open questions** | What we do not know and cannot settle by reading the code — each with what it would change and what would answer it. Omit the section when there is nothing honest to put in it |
| **8. Verify** | Commands and tests that pin the claims above, plus how to build fixtures for this format |
| **9. References** | External spec sections *with numbers*, our investigations, upstream issues |

Four rules the shape depends on:

- **Never separate a structural fact from its consequence.** The strongest grouping force
  in the ZIP material was causal — one property generated eight downstream behaviours. A
  separate "consequences" section breaks the chain and makes the reader re-derive it.
- **No performance numbers.** They are the most volatile thing on the page and they rot
  into a fourth disagreeing source. Verify carries the command instead.
- **Behaviour here, status behind the link.** The page says what a caller sees and how
  fixable it is; `open-issues.md`, `threat-model.md` and `known-issues.md` keep the rest.
- **Test pointers live on the handbook page only**, in §8 — not duplicated into
  `openspec/specs/`. That is step 1 of
  [`discussions/2026-09-specs-to-handbook-and-tests.md`](discussions/2026-09-specs-to-handbook-and-tests.md)
  read literally, and it keeps one list to maintain rather than two that drift.

Stream formats (brotli, lzma, …) get one page each and may need a different shape; take
this as the starting point, not a template to satisfy.

### Topic pages

Topic pages are **not** format pages with the nouns swapped, and
[`topics/prefixed-archives.md`](topics/prefixed-archives.md) — written alongside `zip.md`
and shaped by it — came out looser: shapes in the wild, the mechanism and its tiers, the
cost argument, *where the formats differ*, sharp edges, decisions, references. Take the
conventions rather than the section list: the where-it-lives tags, no performance numbers, status
behind the register link.

The split that matters is the same one in both directions. **A format page keeps what the
format's own structure decides; the topic page keeps the shared machinery.** For prefixed
archives that put the cue set, the scan bound, the budget tiers and the validation argument
on the topic page, and left ZIP with its needle, its validator and its two offset
conventions — because those follow from ZIP locating itself from the end, and no other
format has them.

A topic page may name format behaviour freely where that is what explains the mechanism;
what it must not do is restate a format page or a register. The reverse is also true — a
format page links the topic and keeps the residue, which is why the pipeline subsections in
§2 are where those links naturally sit.

Optional `formats/README.md` / `topics/README.md` indexes may appear alongside the first
page; do not add empty stubs ahead of content.

**Docs with code:** if a PR makes a handbook or published-doc **claim false**, update that
page in the **same PR**. Do not mint a new ADR or OpenSpec essay just to record the
change of mind.

**Deferred design:** migrate dense OpenSpec scenarios into tests + handbook principles
(thin as you go on every spec-touching PR). Direction:
[`discussions/2026-09-specs-to-handbook-and-tests.md`](discussions/2026-09-specs-to-handbook-and-tests.md).
Specs remain the authoritative machine contract until that migration has proven out
(DP1 = C).

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

**Voice:** apply [`unslop`](../.claude/skills/unslop/SKILL.md) to the packet and any
chat around it ([`AGENTS.md`](../AGENTS.md) §Communicating with the maintainer).

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
| Unslop chat / packets / PR comments (default) | `/unslop` — standing rule in [`AGENTS.md`](../AGENTS.md); thin skill, not technical-writing |
| User-facing or handbook prose craft | `/technical-writing` (then `/unslop` on the same prose) |
| Review (other agent) | Cursor: `/code-review` (project command → archivey skill). Elsewhere: **`/code-review-skill`** — never bare `/code-review` (that is a host builtin). Full PR handoff; packets to maintainer |
| Address review | Cursor: `/address-review`. Elsewhere: **`address-review-findings`** / ask for that skill by name |
| PR babysitting | `steward` as today |

Desktop-only extras (not Cloud Agents): install the Cursor **pstack** plugin from the
marketplace if you want poteto playbooks; do not vendor it into this repo. Matt’s pack:
prefer the archivey-adapted skills above over installing the full tree.
