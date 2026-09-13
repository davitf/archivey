## Why

Phase 3's second half. `docs-ia-unpublish-maintainer-tree` (#221) moved maintainer
material out of the site; this reshapes what is left. `usage.md` is 274 lines doing
five jobs, the CLI has a 271-line spec and no nav entry, `gotchas.md` restates four
sections of `costs.md` verbatim, and `extracting.md` — carrying `VISION.md`
claim #1 and backed by the largest spec in the tree — is the thinnest page on the
site.

`review/docs/outline.md` is the worklist: 16 pages, each with purpose, sections in
order, and `file:lines` sources.

## What Changes

- **Split `usage.md` five ways** → `install.md`, `opening-and-listing.md`,
  `reading-members.md`, `errors-and-diagnostics.md`, `cli.md`.
- **Rename `costs.md` → `access-and-cost.md`** and absorb the cost half of
  `gotchas.md`.
- **Grow `extracting.md` ~3×** by absorbing the extraction half of
  `gotchas.md`, the user half of `dev-docs/threat-model.md`, and `SECURITY.md`'s
  caller-hardening notes.
- **Shrink `gotchas.md`** from 155 lines to a two-section digest: one line plus a
  link per entry.
- **Split ADR 0014** three ways (D5) and **`threat-model.md`** two ways (D8).
- **Relocate two sections** that were misfiled regardless: the dedupe recipe to
  `formats.md` beside the stored-digest matrix, and one-shot extract down to a
  cross-link since its code block duplicates `extracting.md`.
- **`AGENTS.md` becomes canonical**, `CLAUDE.md` a pointer plus Claude-specific
  environment notes (D6), fixing two false statements (O-1).
- **Four `documentation` spec deltas**: the site-IA page enumeration and the
  Gotchas-after clause, the listing requirement that names `docs/usage.md`, and the
  Gotchas coverage requirement D4 supersedes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `documentation` — the page set the site presents, which page Gotchas follows,
  where the dual-listing contract is documented, and what Gotchas is responsible
  for.

## Impact

- **Files:** 5 pages created, 1 renamed, 3 split, 1 shrunk, nav rewritten. No `src/`
  changes; no public API surface.
- **New prose:** deliberately minimal — see design.md Decision 1. The ~495 lines the
  outline identifies are Topic 8's, and `how-it-works.md` is not created here.
- **CI:** `scripts/check_docs_nav.py` and `mkdocs build --strict` must stay green
  across a 15-entry nav.
- **Tests:** none. No behaviour changes.
