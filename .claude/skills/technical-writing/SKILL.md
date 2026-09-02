---
name: technical-writing
description: |
  Diátaxis structure and plain developer sentences for docs/ and handbook prose.
  Use when writing or reviewing published docs, handbook pages, PR descriptions, or
  commit messages; or when the user invokes /technical-writing. For strip-AI-tells
  alone (chat, packets), use the unslop skill instead — do not load this skill for that.
---

# Technical writing (archivey)

Condensed from poteto/pstack `technical-writing` (MIT). Apply to **published** `docs/`
and to maintainer handbook prose that a human will read. Code comments still follow
`CONTRIBUTING.md` (why, not what).

**Unslop is a separate skill** — [`../unslop/SKILL.md`](../unslop/SKILL.md). Apply it
to the same prose (and always to maintainer chat / packets per `AGENTS.md`). Do not
duplicate that checklist here so everyday sessions need not load this file.

Pair workflow: [`dev-docs/pair-workflow.md`](../../../dev-docs/pair-workflow.md).
Doc placement: `CONTRIBUTING.md` §“Where does a new doc go?”.

## 1. Pick one Diátaxis mode

| Mode | When | Voice |
| --- | --- | --- |
| Tutorial | Learning by doing | “we”; every step shows a result |
| How-to | Goal-directed steps for a competent reader | Imperatives; no teaching digressions |
| Reference | Lookup facts | Dry, complete, no persuasion |
| Explanation | Why / trade-offs | Opinion allowed; one bounded topic |

Don’t mix modes in one page. Split and link. Archivey’s `docs/` is mostly how-to +
reference; `docs/philosophy.md` is explanation.

## 2. Sentence craft

- Cut every word that does no work. Short everyday words (“use”, not “utilize”).
- Talk to the reader as “you”, present tense. Name the real symbol/path/flag.
- One thought per sentence; mix sentence length so it doesn’t sound machine-cut.
- Conditions before instructions. Common case first.
- No “simply” / “just” / “easy” in procedures.
- Then apply [`unslop`](../unslop/SKILL.md).

## 3. Archivey checks

- User-facing fact → `docs/` + `mkdocs.yml` nav in the same commit.
- Maintainer current truth → `dev-docs/formats/<format>.md` or
  `dev-docs/topics/<topic>.md` (create on first need; rewrite in place).
- Do not dump lab evidence into living pages — link `dev-docs/investigations/`.
- Same PR as the code when the change falsifies a doc claim.
