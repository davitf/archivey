# Worker G — Packaging and platform

You are a **verification worker** for Topic 8 pass 1.
Verify only. Do not edit `docs/` or `src/`.

## Lessons
Spec line drift → match by title. `[code]` → run. Prefer spec (O-26).
Defect/silence true → verified. Harvest capped (~6–10).
`cfg` / platform: name what you checked. Free-threading claims may be
`unverifiable` if you cannot run 3.13t — say so.

## Inputs
- SESSION.md, cluster-G.md (G-1… — all rows in file)
- Spec: `packaging-and-extras` primarily

## Outputs
1. `/workspace/review/docs-content/worker-inputs/verdicts-G.md`
2. `/workspace/review/problem-catalogue/harvest/G-packaging.md`

Use `uv run --no-sync`. Summary: counts, wrong IDs.
