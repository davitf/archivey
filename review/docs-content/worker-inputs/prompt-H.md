# Worker H — Command line

You are a **verification worker** for Topic 8 pass 1.
Verify only. Do not edit `docs/` or `src/`.

## Lessons
Spec line drift → match by title. `[code]` → run CLI commands as written.
Prefer `openspec/specs/cli` (O-26). Defect/silence true → verified.
Terminal escaping / #236 coverage: verify what the claim asserts.
Harvest capped (~5–8).

## Inputs
- SESSION.md, cluster-H.md (H-1…H-17)
- Spec: `cli`

## Outputs
1. `/workspace/review/docs-content/worker-inputs/verdicts-H.md`
2. `/workspace/review/problem-catalogue/harvest/H-cli.md`

Use `uv run --no-sync` / `uv run --no-sync archivey` / `python -m archivey` as needed.
Summary: counts, wrong IDs.
