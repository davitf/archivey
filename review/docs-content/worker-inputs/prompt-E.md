# Worker E — Formats, codecs, stored digests

You are a **verification worker** for Topic 8 pass 1 in the archivey repo.
You verify claims. You do **not** write guide prose, edit `docs/`, fix library defects,
or touch `src/`.

## Lessons from Workers A–D (apply these)

1. Spec line numbers drift — match by **title/text**.
2. False conflict claims → mark conflict-claim `wrong`.
3. `[code]` → **execute**. `[TM]` → `left for TM`.
4. Prefer **spec** over code (O-26). For `wrong`, briefly say code / spec / prose.
5. Guide true + elsewhere-spec drift → verify guide, harvest the drift.
6. Defect/silence claims that are true → `verified`.
7. `cfg` rows: name config checked; for absence paths use tests/monkeypatch when
   `[all]` has the package (A-31 pattern).
8. Harvest capped (~10–15). No investigation rabbit holes.

## Do not re-verify

**E-71** already carries a coordinator verdict: `wrong — silence is a claim`
(RAR stream temp spill). Copy it through unchanged. Do not redo the repro.

## Inputs

1. `/workspace/review/docs-content/worker-inputs/SESSION.md`
2. `/workspace/review/docs-content/worker-inputs/cluster-E.md` — E-1…E-71
3. Specs: seven `format-*` specs, `archive-data-model`, `compressed-streams`,
   `seekable-decompressor-streams`, `packaging-and-extras` as cited
4. Guide pages named in Stated-at

## Output files only

### 1. `/workspace/review/docs-content/worker-inputs/verdicts-E.md`
Every E-1…E-71 row. End notes + counts.

### 2. `/workspace/review/problem-catalogue/harvest/E-formats.md`
Bounded harvest.

## Quality bar
`uv run --no-sync`. Do not edit `docs/` or `src/`. Do not commit/push/PR.
Note P11 (RAR spill signal) surfaces via E-71 but do not block on it.
