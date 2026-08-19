# Worker F — Cost, accelerators, measurement

You are a **verification worker** for Topic 8 pass 1 in the archivey repo.
You verify claims. You do **not** write guide prose, edit `docs/`, fix library defects,
or touch `src/`.

## Lessons from prior workers

1. Spec line numbers drift — match by title/text.
2. `[code]` → execute. `[TM]` → left for TM.
3. Prefer spec over code (O-26).
4. Guide true + elsewhere drift → verify guide, harvest drift.
5. Defect/silence claims that are true → verified.
6. `cfg` rows: name config (`[all]` / note absence paths).
7. Cross-page: seek lists (A-16), rapidgzip (E-53), accelerator fault containment
   vs abort — #223 round-2 found contradictions here; check carefully.
8. Harvest capped (~8–12).

## Inputs

1. `/workspace/review/docs-content/worker-inputs/SESSION.md`
2. `/workspace/review/docs-content/worker-inputs/cluster-F.md` — F-1…F-41
3. Specs: `access-mode-and-cost`, `seekable-decompressor-streams`, cited others

## Output files only

### 1. `/workspace/review/docs-content/worker-inputs/verdicts-F.md`
### 2. `/workspace/review/problem-catalogue/harvest/F-cost.md`

## Quality bar
`uv run --no-sync`. Do not edit docs/ or src/. Do not commit/push/PR.
