## Context

Phase 3 of the docs IA review, second of two changes. The first
(`docs-ia-unpublish-maintainer-tree`, #221, archived #222) moved 35 maintainer files
to `dev-docs/`, established the invariant that everything under `docs/` is published
and for users, and landed `scripts/check_docs_nav.py` to enforce it.

`review/docs/outline.md` is the worklist and was reviewed on #223. Decisions it
carries that bind this change:

| Ref | Decision |
| --- | --- |
| D4 | Gotchas is a two-section digest — one line + a link per entry — with a normative inclusion rule |
| D5 | ADR 0014 splits three ways; the user guarantee lands in the guide |
| D8 | `threat-model.md` splits three ways; the enforced-guarantees prose feeds `safe-extraction.md` |
| D6 | `AGENTS.md` canonical, `CLAUDE.md` a pointer |
| D-a | Nav order: `… Reading members → Gotchas → Safe extraction → Access and cost …` |
| D-b | `reading.md` splits into `opening-and-listing.md` + `reading-members.md` |
| D-c | The config screen is a section on `access-and-cost.md`, not a page |

## Goals / Non-Goals

**Goals:**

- Every page in the guide does one job, and the nav says which.
- The material already written lands where the outline says, without being rewritten
  on the way.
- The specs describe the page set that actually ships.

**Non-Goals:**

- The ~495 lines of new prose the outline identifies (Decision 1).
- `how-it-works.md` (Decision 2).
- Accuracy fixes against the code. O-2's rapidgzip drift and the rest of
  `observations.md` are Topic 8's, and are not opportunistically fixed in a move
  diff — the same argument that kept #221 mechanical.

## Investigations

### Where the 274 lines of `usage.md` go

| Block | Lines | Destination |
| --- | ---: | --- |
| Install | 3-18 | `install.md` |
| Open and list · damaged archives | 20-55 | `opening-and-listing.md` |
| Read a member | 57-83 | `reading-members.md` |
| One-shot extract | 85-93 | `reading-members.md`, reduced to a cross-link |
| Detect without opening | 95-100 | `opening-and-listing.md` |
| Streaming mode | 102-111 | `reading-members.md` |
| Cheap dedupe | 113-143 | **`formats.md`**, beside the stored-digest matrix |
| Duplicate names | 145-173 | `opening-and-listing.md` |
| Passwords | 175-183 | `opening-and-listing.md` |
| Error handling | 185-217 | `errors-and-diagnostics.md` |
| CLI | 219-266 | `cli.md` |
| Next | 268-274 | dropped — the nav is the next-steps list |

### What `gotchas.md` loses, and where each removed line survives

The page goes from 155 lines to a digest. Every removed section has a surviving home,
which is the check that makes the deletion reviewable:

| Section | Lines | Survives on |
| --- | ---: | --- |
| Seeking and redecompression | 13-25 | `access-and-cost.md` §Seeking (already there) |
| Solid archives and open order | 27-37 | `access-and-cost.md` §Solid (already there) |
| Listing completeness vs damage | 39-47 | `opening-and-listing.md` §Damaged archives |
| Streaming mode is one pass | 49-58 | `access-and-cost.md` §Streaming |
| Passwords that look accepted | 60-70 | Kept, compressed to two digest lines |
| Format limitations | 71-89 | `formats.md`, per-format sections |
| Names, duplicates, hardlinks | 91-102 | `safe-extraction.md` §Names, `opening-and-listing.md` |
| Extraction | 103-126 | `safe-extraction.md` |
| Native libraries and process risk | 128-144 | Kept as digest lines |
| What we can only warn about | 146-155 | Dropped (D4: meta section is OUT) |

## Decisions

### 1. Moves now, new prose in Topic 8 — with three named exceptions

The outline identifies ~495 lines of prose no merge can supply. Writing it here would
turn a diff that can be checked by reading section headings into one that cannot,
which is the argument that split phase 3 in the first place and kept #221 mechanical.

So this change moves blocks. Pages that need new prose ship thin and get filled by
Topic 8: `install.md` without its `format_availability()` section,
`errors-and-diagnostics.md` without the diagnostics narrative,
`safe-extraction.md` without the bounded-recursion recipe.

Three exceptions, because the alternative is shipping something wrong rather than
something thin:

1. **A one-or-two sentence orientation on each new page.** A page that opens
   mid-thought because its first line used to be a `##` under something else is not
   "thin", it is broken.
2. **The Gotchas digest lines.** D4's shape is *one line plus a link* per entry.
   Those lines are the page; there is no move-only version of it.
3. **The four D8 residual one-liners** (O6 nesting, O1 unguarded paths, O8 header
   encryption, O2 collisions), which D8 routes to Gotchas explicitly.

**Rejected:** writing everything now (unreviewable), and creating empty pages with
TODO markers (publishes a placeholder to real readers, the same objection that
deferred `how-it-works.md`).

### 2. `how-it-works.md` is not created here

Carried from `2026-08-03-docs-ia-unpublish-maintainer-tree/design.md` Decision 4 and
re-raised on #223, where an automated review recommended a nav stub. The answer is
unchanged: the page is 100% new prose, and an empty published page breaks the
invariant the migration exists to establish.

**Consequence:** the nav ends this change at **15 entries**, not the outline's 16.
The outline describes the destination; this is a step toward it.

### 3. Splitting ADR 0014 leaves a stub, not a deletion

D5 splits 615 lines three ways: a ~30-line ADR, an investigation file, and the user
guarantee. The ADR keeps its number and filename — six other ADRs and
`decisions/index.md` cite it by path, and a renumber would break provenance for a
cosmetic gain. Its `## Open questions` section (O-6) moves to the investigation file
rather than being resolved here: an accepted ADR should not carry open questions, but
closing them is a `verification-integrity-mode` decision, not a docs one.

### 4. `costs.md` → `access-and-cost.md` is a rename, committed separately

Done as its own commit with no content edits so `git log --follow` stays exact, then
the absorption lands on top. The same discipline as #221's commit 2.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| A `gotchas.md` line is deleted with no surviving home | The Investigations table above is the checklist; each removed section names its destination, and the reviewer can grep for it |
| Pages ship thin and Topic 8 never happens | The guide is still strictly better than today — nothing published becomes *wrong*, only shorter than planned. `review/backlog.md` Topic 8 and the outline's own worklist both record what is owed |
| Five new pages mean five new sets of inbound links to get wrong | `scripts/check_docs_nav.py` covers nav completeness and repo URLs; `mkdocs build --strict` covers site-relative cross-references; the two together are why the guardrails shipped first |
| The user guarantee (ADR 0014) reads as a spec dump on a user page | It is the only copy of that contract, and D5 says do not leave it only in `dev-docs/`. Tightening its wording is explicitly Topic 8's |

## Open Questions

None blocking. Recorded for Topic 8: the outline's §Still-open item 2 —
whether `how-it-works.md` is worth its ~150 lines at all, or whether the decisions
summary belongs distributed across the pages that raise each question.
