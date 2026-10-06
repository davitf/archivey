---
name: write-user-docs
description: |
  Write or rewrite a user-facing docs page with the maintainer, one agreed paragraph
  at a time: build the pile of facts, verify each by running code, offer options per
  paragraph, show the final text before committing. Merges five writing skills into one
  voice where "sounds like a person" outranks Simplified Technical English.
  Use when: writing or rewriting a page in docs/ or new_docs/, "grill me on the docs",
  drafting user documentation with the maintainer, or when the user invokes
  /write-user-docs.
---

# Write user docs

How the `new_docs/` rewrite was written (PR #523, started 2026-09-29), kept so the next
pages are written the same way. The maintainer found the old `docs/` too flat, too
prescriptive and too detailed. This process fixed that by working in small agreed steps
and by putting one writing goal above the rest: the page must read as if a person wrote it.

This skill is for **published user docs**. Chat, PR comments and briefs still use
[`unslop`](../unslop/SKILL.md). Code comments and exception messages still use
[`asd-ste100`](../asd-ste100/SKILL.md) in strict mode ([`AGENTS.md`](../../../AGENTS.md)
§Writing English).

## The loop

1. **Build the pile first.** List the facts a user needs, mined from `docs/`, the code and
   the tests, and sort them into tiers: the first page, concept pages, and reference. Write
   down who the reader is walking in (writes Python, knows what a ZIP is, has never heard
   of a solid archive) and what they don't know yet. Keep the pile in a notes file, not in
   the PR.
2. **Agree the order.** At site level, each page is one step in the reader's journey and
   says what it grounds for later pages. At page level, propose the sections and their
   order and let the maintainer reorder. The order can change later, so start writing once
   it's roughly right.
3. **One paragraph at a time.** For each paragraph or block, offer two or three options,
   labelled a, b, c. Each option is the actual draft text or a one-line description of
   it, and your recommendation comes first with one line of why. Also argue the format:
   prose, a list, a table, or a code block.
4. **Show the final text before committing it.** Once the maintainer picks and edits,
   post the paragraph as it will appear, and commit only after they accept it. If you
   committed early, say so and post it anyway for edits. Don't roll back.
5. **Commit each agreed section** to the page's draft PR, so the PR always holds only
   agreed text. Read the maintainer's inline PR comments too. Reply on each thread, fix
   it, and resolve it.
6. **When a page is done**, ask for a review (the `review` label) for fresh eyes, merge
   it, and start the next pages in a new PR.

## Verify every behaviour claim by running it

Docstrings and old docs drift. During #523, the `extract_all` docstring still described
the filter order from before a merged change, and several first guesses were wrong:
Windows reserved names are refused rather than renamed, and path refusals apply under
every policy rather than per policy. So:

- Build a tiny archive with the unusual case in it and run it through the API under each
  option. Use `uv run python`, not bare `python3`. Keep the scratch script outside the
  repo, and delete any files it drops in the working tree.
- When a table states behaviour per option, every cell comes from a run.
- When a merged PR changes behaviour the page describes, rerun the checks and update the
  page. If the source has drifted too, fix it in the same docs PR.

## Voice

Precedence when rules disagree: **the maintainer > sounds like a person > grounding >
Diátaxis > sentence rules > STE.**

Rulings from the maintainer in #523's thread:

- Describe behaviour, don't command facts. Imperatives belong only in real step-by-step
  procedures. Write "each stream works only until the loop moves on", not "read each
  stream inside the loop".
- No imperative-as-conditional. Write "If you use X, Y happens", never "Use X and Y
  happens".
- Never use a term before the page defines it. A first page may stay vague ("on some
  archives this is slow") and link to the page that explains why.
- Don't name a mechanism the reader hasn't met. Instead of "checksum", write "damage may
  go unnoticed".
- Watch for wording that suggests the wrong concept. "Limits apply to one archive at a
  time" reads as concurrency, so write "limits apply to each archive separately".
- Be honest about what a workaround can't do. Write "be careful with archives inside
  archives", not "bound the total yourself" when the user can't bound the ratio.
- Say "best effort" when something is best effort. Example: `on_progress` can stop an
  extraction, but only between chunks.
- Headings name the topic plainly ("Extracting", not "Safe extraction").
- If a section is getting too detailed for its subject, move it to its own page and
  leave a one-line pointer behind. Don't link to a page that doesn't exist yet.

Mechanics:

- No em dashes. Use commas, full stops, or a colon introducing a list.
- Never put a bidi control character in a doc or a chat message. Describe it in words,
  because a real one reverses the rest of the line where it's displayed.
- Tables for option → effect and example → outcome. Show defaults in a code block with
  one short comment per argument, then explain the values.
- Mention that string options are enum values once, where the options are introduced.
- Mix sentence lengths. A short paragraph is fine.

## What the five source skills each contribute

The process merges five external skills. None of them is vendored here. This section is
what was kept from each.

| Source | Kind | Kept |
|---|---|---|
| mattpocock `writing-beats` | process | Grow the text one agreed move at a time, chosen from 2-3 candidates, at site level (page order) |
| mattpocock `writing-shape` | process | The same loop per paragraph, including the argument about format. Grounding: no block leans on an idea the reader hasn't met |
| blader `humanizer` | style | The top goal: the result must sound like a person. Its tell catalogue, ranked by strength |
| pstack `unslop` | style | Cuts puffery, throat-clearing and ornaments. Already in this repo as [`unslop`](../unslop/SKILL.md) |
| pstack `technical-writing` | style | Pick a Diátaxis mode per page, short everyday words, conditions before instructions. Already here as [`technical-writing`](../technical-writing/SKILL.md) |

Where they conflicted, this is how it was settled:

- **Diátaxis vs grounding.** On a usage page, grounding wins. A first page may explain a
  concept in a clause, or stay vague and link out, rather than lean on an undefined term.
- **Punctuation.** No dashes. Colons only to introduce a list or example. Parentheses only
  as a whole grammatical unit.
- **STE strictness.** STE's caps (one instruction per sentence, word limits, avoid -ing)
  are a check for real procedures and warnings only. Applied to whole pages they produced
  the flat docs this rewrite replaced.
- **The pile.** Both process skills assume the exploring is done. Here it wasn't, which is
  why step 1 builds the pile before any writing starts.
