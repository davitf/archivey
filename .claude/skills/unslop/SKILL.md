---
name: unslop
description: |
  Cut AI tells from maintainer-facing prose (chat, decision packets, PR comments,
  briefs). Standing default voice per AGENTS.md. Use when writing to the maintainer,
  when the user invokes /unslop, or when asked to strip LLM filler. For ambiguity use
  asd-ste100; for a user docs page use write-user-docs.
---

# Unslop (archivey)

Condensed from poteto/pstack `unslop` (MIT). The fact, edit-mode and density rules and
the last five tells below are adapted from Every's `ce-noslop`
([compound-engineering-plugin](https://github.com/EveryInc/compound-engineering-plugin),
MIT, Copyright (c) 2025 Every). Checklist only — no Diátaxis.

Standing rule: [`AGENTS.md`](../../../AGENTS.md) §Writing English, which also says
where [`asd-ste100`](../asd-ste100/SKILL.md) applies. That skill cuts ambiguity; this
one cuts AI tells. Run both on maintainer-facing prose. User docs pages:
[`write-user-docs`](../write-user-docs/SKILL.md).

Rewrite until nothing reads like default LLM filler:

- Drop puffery (“robust”, “seamless”, “comprehensive”, “leverages”).
- Drop throat-clearing (“It is important to note that”, “In order to”).
- Avoid stacked em-dashes, decorative bold lead-ins, emoji ornaments, synonym cycling.
- Prefer specific claims (“rename breaks the build”) over vague ones (“can cause issues”).
- Have a point of view when explaining trade-offs; stay dry in reference dumps.
- Cut every word that does no work. Short everyday words (“use”, not “utilize”).
- Drop “not X but Y” when nobody claimed X (“it’s not a linter, it’s a system”). State Y.
- Name the operation, not a borrowed metaphor: a “surface” that is an API, a “lever”
  that is a change, a “primitive” that is a function.
- Put the decision first: outcome, then reason, then background.
- Cut process narration in a report: the steps you took that do not change what the
  reader does next. A cause ruled out or a fix that failed stays, as a finding.
- Cut manufactured thoroughness: bare counts (“resolved 11 threads”), scorecards, lists
  of everything checked. Say what was decided and why.

Three rules on top of the list:

- **Every source fact survives.** A rewrite keeps each number, name, path, qualifier,
  quote and link of the original. Plain text that drops a qualifier has failed.
- **When editing existing text, change only the sentences that fail a check.** A
  sentence that passes stays as written, so a second pass over your output changes
  nothing.
- **Flag a passage only at three or more patterns**, or one pattern repeated across
  passages. One device is a choice, not a tell. This is advice, not a gate
  ([`AGENTS.md`](../../../AGENTS.md) §Writing English).

Self-audit: “What still looks AI-generated?” Fix that next.
