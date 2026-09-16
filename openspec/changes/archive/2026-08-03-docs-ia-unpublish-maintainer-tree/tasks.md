## 1. Delete the root stubs

- [x] 1.1 Delete `ARCHITECTURE.md`, `ASYNC.md`, `COMPARISON.md`, `SPEC.md` (D10)
- [x] 1.2 Repoint `IDEAS.md:6` and any other reference that pointed at a stub

## 2. Move the maintainer tree

Pure renames — no content edits in these commits, so `git log --follow` stays exact.

- [x] 2.1 `git mv docs/internal/{index,threat-model,open-issues,known-issues,library-analysis,release-checklist,release-repo-cutover}.md dev-docs/`
- [x] 2.2 `git mv` the five finished investigations (`ppmd-*` ×3, `pyppmd-upstream-report`, `rapidgzip-upstream-report`) to `dev-docs/investigations/`
- [x] 2.3 `git mv docs/grab-bag/{index,SPEC,ARCHITECTURE,COMPARISON,ASYNC}.md dev-docs/history/` and `parallel-reader.md` to `dev-docs/investigations/`
- [x] 2.4 `git mv docs/decisions/ dev-docs/decisions/` (D2 — raw ADR log unpublished)
- [x] 2.5 `git mv PLAN.md IDEAS.md dev-docs/` (D7)
- [x] 2.6 Delete the `Decisions` / `Internal` / `Grab-bag` nav sections from `mkdocs.yml` (27 entries)
- [x] 2.7 Verify `git status` reports renames, not delete+add

## 3. Repoint inbound references

- [x] 3.1 `src/archivey/**` — 11 comments **and the two runtime error-message strings** in `streams/decompress.py` (a stale path here is user-visible)
- [x] 3.2 `tests/**`, `scripts/**`, `.github/workflows/**`, `pyproject.toml` comments
- [x] 3.3 Root docs: `README`, `CHANGELOG`, `SECURITY`, `CONTRIBUTING`, `VISION`, `AGENTS`, `CLAUDE`, and the moved `dev-docs/PLAN.md` / `dev-docs/IDEAS.md`
- [x] 3.4 `openspec/project.md` header paths (`grab-bag/`, `docs/decisions/`)
- [x] 3.5 `.claude/skills/code-review-skill/reference/**`
- [x] 3.6 Leave `review/**` and `openspec/changes/archive/**` untouched — they describe the tree as it was
- [x] 3.7 `grep -rn 'docs/internal/\|docs/grab-bag/\|docs/decisions/' .` returns hits only under `review/` and the archives

## 4. Resolve published-page links (D3 / D2)

- [x] 4.1 The nine internal/grab-bag links: 4 dropped, 5 become absolute `blob/main/` URLs, per `DECISIONS.md` D3's table
- [x] 4.2 `docs/index.md:47-48` — rewrite the "For contributors" block as a short repo pointer, dropping the "Decision log" nav pointer
- [x] 4.3 The ten user-page ADR links (`acknowledgements` ×4, `migrating` ×3, `support-matrix` ×2, `usage` ×1): inline the end-user one-liner and drop the link; keep an absolute URL only where the depth cannot be inlined
- [x] 4.4 `SECURITY.md:73` and `VISION.md:28` repoint at `dev-docs/threat-model.md`

## 5. Guardrails

- [x] 5.1 Add `scripts/check_docs_nav.py`: every `docs/**/*.md` in nav, every nav entry a real file, every `blob/main/<path>` URL in `docs/**` and `README.md` an existing repo path
- [x] 5.2 Wire it into the `docs` CI job ahead of the strict build
- [x] 5.3 Verify it fails on a planted violation (an unlisted page and a dead `blob/main/` URL), then remove the plants
- [x] 5.4 Add the "where does a new doc go?" placement rule to `CONTRIBUTING.md`, next to "Working with the specs"

## 6. Record the follow-ups

- [x] 6.1 `review/backlog.md` — the `known-issues.md` triage obligation (D9), so it survives the review being archived
- [x] 6.2 `review/STATUS.md` — docs review moves to phase 3, and note what the follow-up change owns (splits, `how-it-works.md`, the D4 Gotchas delta, the `AGENTS`/`CLAUDE` merge)
- [x] 6.3 `review/docs/inventory.md` §Migration mechanics — refresh the nine-commit table, which predates D2 and has no row for the ADR move

## 7. Verify

- [x] 7.1 `uv run --group docs mkdocs build --strict` green, and `check_docs_nav.py` clean
- [x] 7.2 `uv run pyrefly check` and `uv run ty check` clean (comment/string edits only, but the error strings are in typed code)
- [x] 7.3 Test suite in all three dependency configurations per `CONTRIBUTING.md`
- [x] 7.4 `openspec validate --strict docs-ia-unpublish-maintainer-tree`
- [x] 7.5 Dry-run archive on a scratch tree; diff `openspec/specs/` to confirm both MODIFIED deltas target requirements that exist, then reset
