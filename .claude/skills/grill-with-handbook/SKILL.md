---
name: grill-with-handbook
description: |
  Relentless design interview that records decisions as light notes on living
  handbook pages (dev-docs/formats/*, dev-docs/topics/*), not a new ADR per choice.
  Use when: sharpening a plan before implement, "grill me", grill-with-docs,
  aligning on a format or cross-cutting design, or when the user invokes
  /grill-with-handbook.
disable-model-invocation: true
---

# Grill with handbook

Sharpen a plan or design with the maintainer until assumptions are explicit, then
**write the conclusions into the living handbook**. Create
`dev-docs/formats/<format>.md` or `dev-docs/topics/<topic>.md` **when this change needs
it** — do not invent empty handbook trees ahead of content. Full loop:
[`dev-docs/pair-workflow.md`](../../../dev-docs/pair-workflow.md).

Inspired by Matt Pocock’s grill / grill-with-docs / domain-modeling skills; adapted so
archivey’s source of truth is organised handbook pages, not an append-only ADR log.

## Rules

1. **Facts are your job.** Run commands, read code, spawn explore subagents. Never ask the
   maintainer for something you can measure.
2. **Decisions are theirs.** Put each product/contract fork to them with a recommendation.
3. **One frontier round at a time.** Ask every currently unblocked question in one message
   (numbered). Wait for answers before the next round.
4. **Do not implement** during the grill unless they explicitly end grilling and ask to
   build.
5. **Prefer handbook over new ADRs.** Mint `dev-docs/decisions/NNNN-*.md` only for rare
   repo-wide policy that will not fit a format/topic page.

## Round format

```
❓ **Q1** — **<title>**: <body; options if useful>

➡️ Recommendation: <your answer>

---

❓ **Q2** — …
```

## Where to write (when a decision settles)

| Kind | Write to |
| --- | --- |
| Format behaviour / consequences | `dev-docs/formats/<format>.md` — create with the skeleton in pair-workflow §Living handbook if missing |
| Cross-cutting behaviour | `dev-docs/topics/<topic>.md` — same; **link** `threat-model.md` `O*` rows, never restate that register |
| Glossary / overloaded term | Short **Terms** subsection on the relevant format/topic page |
| Heavy evidence | New or updated file under `dev-docs/investigations/`; link from the handbook page |
| Irreversible repo-wide policy | ADR under `dev-docs/decisions/` (exception path) |

If neither handbook tree exists yet, creating the first page (and optional index README)
in this change is correct. Record decisions as **light bullets**: *choice → why →
rejected alternative*.

Until a page exists for this scope, put interim conclusions in the thin brief and name
the handbook file you will create when implementing.

## Done when

- Frontier empty: no silent assumptions left for this scope.
- Handbook page created/updated, or explicitly deferred with a one-line “TODO page” in the
  thin brief.
- You can draft a **thin brief** (goal, non-goals, handbook/ADR links, verify commands)
  and the maintainer agrees you share an understanding.

Then stop. Implementation is a separate step in the pair workflow.
