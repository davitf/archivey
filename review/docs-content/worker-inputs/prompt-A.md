# Worker A — Opening, detection, sources

You are a **verification worker** for Topic 8 pass 1 in the archivey repo.
You verify claims. You do **not** write guide prose, edit `docs/`, fix library defects,
or touch `src/`.

## Inputs (read these)

1. `/workspace/review/docs-content/worker-inputs/SESSION.md` — this session's baseline
2. `/workspace/review/docs-content/worker-inputs/cluster-A.md` — your **only** claim rows
3. Specs under `openspec/specs/` named in the Settles-it column:
   `format-detection`, `archive-reading`, `compressed-streams`, plus cited
   `format-*` / `access-mode-and-cost` / `diagnostics` / `documentation` as needed
4. Cited `src/` files and the guide pages named in Stated-at (for `[code]` blocks and
   to confirm what the prose actually says)
5. `review/docs-content/brief.md` §Hard constraints and O-26 (three outcomes)

## Do not re-verify

**A-6** and **A-16** already carry coordinator verdicts. Copy them through unchanged:
- A-6: `wrong` as written (absolute reading)
- A-16: `wrong` — `access-and-cost.md`

## Per-row procedure

For each of A-1…A-44 (except the two above):

1. Read the `Settles it` reference (prefer **spec** over code when both exist).
2. If `[code]`: extract the fenced block from the cited page and **execute** it
   (create any temp fixtures the block needs; use real archives under `tests/` when
   helpful). Record pass/fail and the error if any.
3. If `[TM]`: leave as out-of-scope (there should be none in A).
4. If `cfg`: state which dependency config you checked.
5. If co-cited pages disagree about the same fact: do **not** collapse to one verdict —
   flag the split (A-33 is the live candidate; provenance says it may not reproduce —
   check carefully).
6. Return exactly one of: `verified` | `wrong` | `unverifiable (<reason>)`.

For `wrong`, say briefly whether code, spec, or prose looks wrong (O-26), with evidence.
Do **not** classify into fix vehicles — the coordinator does step 5.

## Output files (write these; nothing else)

### 1. `/workspace/review/docs-content/worker-inputs/verdicts-A.md`

A markdown table:

| # | V | Evidence |
|---|---|---|
| A-1 | verified | `archive-reading/spec.md:N` says …; spot-check with … |
| … | … | … |

Every A-1…A-44 row must appear. No bare gaps.

### 2. `/workspace/review/problem-catalogue/harvest/A-opening.md`

Bounded harvest (cap ~8–12 items). Schema per problem (neutral phrasing where possible):

- **Problem** (what the world does — not archivey vocabulary if avoidable)
- **Symptom**
- **Evidence** (`file:line` / spec / test)
- Optional one-liner on how archivey answers today

No investigation. If you start researching a problem, stop and return to verifying.

### 3. Short notes at the end of verdicts-A.md

- Rows you marked `wrong` (list)
- Any A-33 resolution (contradiction or not)
- Config notes for `cfg` rows
- Anything the coordinator must resolve across clusters

## Quality bar

- Prefer running a small repro over citing a neighbour page.
- Spec line numbers in Settles-it may have drifted; find the requirement by title/text.
- Use `uv run --no-sync` for Python. Fixtures live under `tests/`.
- Do not commit, push, or open a PR.
