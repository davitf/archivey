# Worker B — Reading, member lifetime, concurrency

You are a **verification worker** for Topic 8 pass 1 in the archivey repo.
You verify claims. You do **not** write guide prose, edit `docs/`, fix library defects,
or touch `src/`.

## Lessons from Worker A (apply these)

1. Spec line numbers in Settles-it **drift** — find the requirement by **title/text**,
   not by the cited line number alone.
2. A row titled "Conflicts with X" may be **false** (two pages naming two exceptions for
   *different* situations). Check situations carefully; if the conflict does not
   reproduce, verdict the conflict-claim `wrong`, do not invent a SPLIT.
3. `[code]` means **execute** the fenced block. Create temp fixtures; use `tests/`
   archives when helpful. Record the actual error if it fails.
4. Prefer **spec** over code (O-26). For `wrong`, say briefly whether code / spec /
   prose looks wrong — coordinator classifies vehicles later.
5. Harvest is capped (~8–12). No investigation rabbit holes.

## Inputs

1. `/workspace/review/docs-content/worker-inputs/SESSION.md`
2. `/workspace/review/docs-content/worker-inputs/cluster-B.md` — your only rows
3. Specs: `archive-reading`, `reader-concurrency`, `archive-data-model`,
   `access-mode-and-cost`, `seekable-decompressor-streams`, `safe-extraction` as cited
4. Guide pages named in Stated-at (for `[code]` and confirming prose)

## Rows

Verify **B-1…B-48** (all empty). No coordinator pre-verdicts in B.

## Per-row procedure

Same as A: read Settles-it → decide → `verified` | `wrong` | `unverifiable (reason)`
with evidence. `[TM]` leave out-of-scope. `cfg` name the config checked.

## Output files only

### 1. `/workspace/review/docs-content/worker-inputs/verdicts-B.md`

| # | V | Evidence |
|---|---|---|
| B-1 | … | … |

Every B-1…B-48 must appear. End with: wrong list, cfg notes, cross-cluster flags, counts.

### 2. `/workspace/review/problem-catalogue/harvest/B-reading.md`

Bounded harvest; schema: Problem / Symptom / Evidence / (optional Today).

## Quality bar

- Use `uv run --no-sync`. Do not commit/push/PR.
- Prefer a small repro over citing another page.
- Do not edit `docs/` or `src/`.
