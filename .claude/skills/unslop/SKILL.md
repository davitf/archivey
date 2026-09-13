---
name: unslop
description: |
  Cut AI tells from maintainer-facing prose (chat, decision packets, PR comments,
  briefs). Standing default voice per AGENTS.md. Use when writing to the maintainer,
  when the user invokes /unslop, or when asked to strip LLM filler. Prefer this over
  loading technical-writing unless Diátaxis/docs craft is also needed.
---

# Unslop (archivey)

Condensed from poteto/pstack `unslop` (MIT). Checklist only — no Diátaxis.

Standing rule: [`AGENTS.md`](../../../AGENTS.md) §Communicating with the maintainer.  
Docs/handbook structure + craft: [`../technical-writing/SKILL.md`](../technical-writing/SKILL.md).

Rewrite until nothing reads like default LLM filler:

- Drop puffery (“robust”, “seamless”, “comprehensive”, “leverages”).
- Drop throat-clearing (“It is important to note that”, “In order to”).
- Avoid stacked em-dashes, decorative bold lead-ins, emoji ornaments, synonym cycling.
- Prefer specific claims (“rename breaks the build”) over vague ones (“can cause issues”).
- Have a point of view when explaining trade-offs; stay dry in reference dumps.
- Cut every word that does no work. Short everyday words (“use”, not “utilize”).

Self-audit: “What still looks AI-generated?” Fix that next.
