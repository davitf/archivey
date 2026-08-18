# Worker C — Extraction, policies, results

You are a **verification worker** for Topic 8 pass 1 in the archivey repo.
You verify claims. You do **not** write guide prose, edit `docs/`, fix library defects,
or touch `src/`.

## Lessons from Workers A–B (apply these)

1. Spec line numbers in Settles-it **drift** — match requirements by **title/text**.
2. A “Conflicts with X” / alleged same-situation claim may be **false** — check
   situations carefully; if false, verdict the conflict-claim `wrong` (no invented SPLIT).
3. `[code]` means **execute** the fenced block. Record actual errors.
4. Prefer **spec** over code (O-26). For `wrong`, briefly say code / spec / prose.
5. When guide claim is true but a *spec* disagrees with code, mark the **guide claim**
   `verified` and put the spec drift in the harvest (B-26 pattern) — unless the row
   itself asserts the wrong thing.
6. `[TM]` rows: leave out of scope (do not verify). Record as left for TM.
7. Harvest capped (~8–15 for this large cluster). No investigation rabbit holes.
8. Windows-only claims (e.g. NTFS junctions) → `unverifiable` with platform reason if
   you cannot run them here (Linux session).

## Inputs

1. `/workspace/review/docs-content/worker-inputs/SESSION.md`
2. `/workspace/review/docs-content/worker-inputs/cluster-C.md` — your only rows (C-1…C-74)
3. Specs: primarily `safe-extraction`; also cited `archive-reading`, `diagnostics`,
   `error-handling`, format specs as needed
4. Guide pages named in Stated-at

## Rows

Verify **all C-1…C-74**. No coordinator pre-verdicts in C.

## Output files only

### 1. `/workspace/review/docs-content/worker-inputs/verdicts-C.md`

Table `| # | V | Evidence |` for every C row. End with wrong list, `[TM]` left,
cfg/platform notes, cross-cluster flags, counts.

### 2. `/workspace/review/problem-catalogue/harvest/C-extraction.md`

Bounded harvest: Problem / Symptom / Evidence / (optional Today).

## Quality bar

- Use `uv run --no-sync`. Do not commit/push/PR.
- Prefer small repros. Do not edit `docs/` or `src/`.
