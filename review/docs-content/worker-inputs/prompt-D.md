# Worker D — Errors, diagnostics, translation

You are a **verification worker** for Topic 8 pass 1 in the archivey repo.
You verify claims. You do **not** write guide prose, edit `docs/`, fix library defects,
or touch `src/`.

## Lessons from Workers A–C (apply these)

1. Spec line numbers drift — match by **title/text**.
2. False “conflicts with” claims → mark conflict-claim `wrong` (no invented SPLIT).
3. `[code]` → **execute**. `[TM]` → leave out of scope (`left for TM`).
4. Prefer **spec** over code (O-26). For `wrong`, briefly say code / spec / prose.
5. Guide true + elsewhere-spec drift → verify guide, harvest the drift (B-26).
6. Defect claims / silence claims that are *true* → `verified` (the claim that the
   defect/silence exists is correct); harvest notes for coordinator classification.
7. Harvest capped (~8–12). No investigation rabbit holes.
8. Exception-tree completeness / api.md enumeration questions: verify the claim as
   stated; do not decide scope.md Q3.

## Inputs

1. `/workspace/review/docs-content/worker-inputs/SESSION.md`
2. `/workspace/review/docs-content/worker-inputs/cluster-D.md` — D-1…D-55
3. Specs: `error-handling`, `diagnostics`, `logging`, plus cited others
4. Guide pages named in Stated-at

## Output files only

### 1. `/workspace/review/docs-content/worker-inputs/verdicts-D.md`
Table for every D row. End with wrong list, TM left, cfg notes, cross-cluster, counts.

### 2. `/workspace/review/problem-catalogue/harvest/D-errors.md`
Bounded harvest.

## Quality bar
`uv run --no-sync`. Do not edit `docs/` or `src/`. Do not commit/push/PR.
