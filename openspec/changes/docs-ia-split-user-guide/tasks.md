## 1. Split `usage.md`

Blocks land whole; the only new text is each page's orientation lines (design.md
Decision 1).

- [x] 1.1 `install.md` ← `usage.md:3-18`
- [x] 1.2 `opening-and-listing.md` ← `:20-55`, `:95-100`, `:145-173`, `:175-183`
- [x] 1.3 `reading-members.md` ← `:57-83`, `:102-111`, and `:85-93` reduced to a cross-link
- [x] 1.4 `errors-and-diagnostics.md` ← `:185-217` + the diagnostics note at `extracting.md:90-93`
- [x] 1.5 `cli.md` ← `:219-266`
- [x] 1.6 Delete `usage.md`; drop its `## Next` block (the nav is the next-steps list)
- [x] 1.7 Rewrite the nav: 15 entries in the D-a order

## 2. `costs.md` → `access-and-cost.md`

- [x] 2.1 `git mv docs/costs.md docs/access-and-cost.md` — pure rename, own commit, no content edits
- [x] 2.2 Absorb the cost half of `gotchas.md` (`:13-25`, `:27-37`) where it is not already stated

## 3. Split ADR 0014 (D5)

- [x] 3.1 `dev-docs/decisions/0014-*.md` shrinks to a ~30-line ADR: Context / Decision / Consequences. Keep the number and filename — six ADRs and `decisions/index.md` cite it by path
- [x] 3.2 `dev-docs/investigations/adr-0014-investigation.md` ← the trade-offs, implementation notes, and the `## Open questions` section (O-6)
- [x] 3.3 The user guarantee (`:320-375`) → `reading-members.md` §2

## 4. Split `threat-model.md` and grow `extracting.md` (D8)

- [x] 4.1 Trust boundaries + what is enforced (`dev-docs/threat-model.md:9-58`) → `extracting.md`; drop the D3 repo link that pointed at it
- [x] 4.2 Extraction half of `gotchas.md` (`:103-126`, `:91-102`) → `extracting.md`
- [x] 4.3 `SECURITY.md:68-89` caller-hardening notes → `extracting.md`, with a link back from `SECURITY.md` (O-7)
- [x] 4.4 `dev-docs/threat-model.md` keeps the O/C register only

## 5. Shrink `gotchas.md` to a digest (D4)

- [x] 5.1 Two sections, one line + a link per entry, per the outline §7
- [x] 5.2 The four D8 residual one-liners: O6 nesting, O1 unguarded paths, O8 header encryption, O2 collisions
- [x] 5.3 Drop the "what we can only warn about" meta section (D4: OUT)
- [x] 5.4 **Verify every removed section has a surviving home** — design.md §Investigations is the checklist

## 6. Relocations

- [x] 6.1 Dedupe recipe (`usage.md:113-143`) → `formats.md`, directly after the stored-digest matrix
- [x] 6.2 Drop the now-circular `formats.md` → `usage.md#cheap-dedupe` cross-link

## 7. `AGENTS.md` canonical (D6)

- [x] 7.1 `AGENTS.md` absorbs the shared content from `CLAUDE.md`; fix O-1's two false statements (the CLI ships; native 7z/RAR ship)
- [x] 7.2 `CLAUDE.md` becomes a pointer plus Claude Code–specific environment notes. Do not delete it — Claude Code auto-loads it by name
- [x] 7.3 Keep **both** `openspec` install recipes (global npm and `--prefix "$HOME/.local"` for Cursor Cloud EACCES) — a careless merge drops one

## 8. Repoint and verify

- [x] 8.1 Every inbound link to `usage.md` / `costs.md` across `docs/`, `README.md`, root docs and `dev-docs/`
- [x] 8.2 `grep -rn 'usage\.md\|costs\.md' docs/ README.md *.md` returns nothing outside `review/` and archives
- [x] 8.3 `scripts/check_docs_nav.py` clean; `uv run --group docs mkdocs build --strict` green
- [x] 8.4 `openspec validate --strict docs-ia-split-user-guide`
- [x] 8.5 Dry-run archive on a scratch tree; confirm `+1 ~2 -1` against real requirements, then reset
- [x] 8.6 Update `review/STATUS.md` and `review/docs/outline.md` with what shipped thin and what Topic 8 owes
