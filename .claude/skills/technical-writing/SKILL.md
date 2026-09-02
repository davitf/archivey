---
name: technical-writing
description: |
  Diátaxis structure, plain developer sentences, and unslop (cut AI tells). Use when
  writing or reviewing docs/, handbook prose, PR descriptions, or commit messages; or
  when the user invokes /technical-writing or asks to unslop prose. Unslop alone is also
  the standing voice for maintainer chat and decision packets (see AGENTS.md).
---

# Technical writing (archivey)

Condensed from poteto/pstack `technical-writing` + `unslop` (MIT). Apply the full skill
(Diátaxis + craft + unslop) to **published** `docs/` and to maintainer handbook prose
that a human will read. **Unslop (§3) alone** is the default for maintainer-facing chat,
decision packets, and PR comments — see
[`AGENTS.md`](../../../AGENTS.md) §Communicating with the maintainer. Code comments
still follow `CONTRIBUTING.md` (why, not what).

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

## 3. Unslop

**Always on** for maintainer-facing chat, decision packets, and PR comments
([`AGENTS.md`](../../../AGENTS.md)). For docs/handbook pages, apply together with §§1–2.

Rewrite until nothing reads like default LLM filler:

- Drop puffery (“robust”, “seamless”, “comprehensive”, “leverages”).
- Drop throat-clearing (“It is important to note that”, “In order to”).
- Avoid stacked em-dashes, decorative bold lead-ins, emoji ornaments, synonym cycling.
- Prefer specific claims (“rename breaks the build”) over vague ones (“can cause issues”).
- Have a point of view in Explanation mode; stay dry in Reference.

Self-audit: “What still looks AI-generated?” Fix that next.

## 4. Archivey checks

- User-facing fact → `docs/` + `mkdocs.yml` nav in the same commit.
- Maintainer current truth → `dev-docs/formats/<format>.md` or
  `dev-docs/topics/<topic>.md` (create on first need; rewrite in place).
- Do not dump lab evidence into living pages — link `dev-docs/investigations/`.
- Same PR as the code when the change falsifies a doc claim.
