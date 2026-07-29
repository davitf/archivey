# In-flight review status (2026-07-29)

## In flight

| Review | State |
|---|---|
| [`docs/`](docs/brief.md) — documentation full review | Brief written 2026-07-29. Four-phase process (audit → decide → migrate → guardrail). **Phase 1 commissioned** to a fresh agent; prompt recorded at [`docs/phase-1-prompt.md`](docs/phase-1-prompt.md). Bias control pass **delivered** (#208, `docs/independent/`): headline is a proportional disagreement — safe extraction is 6.3% of our guide vs ~25% proposed. Code-shaped findings filtered into [`code-self-documentation.md`](docs/code-self-documentation.md). |

`debt-ledger/` and `performance/` were archived on 2026-07-28 after the last two ledger
items (**T7** corpus-matrix audit, **T4** half-test) landed and **performance Q4** was
decided.

## What closed the round

| Review | Archived as | Closing work |
|--------|-------------|--------------|
| `debt-ledger/` | `archive/2026-07-28-debt-ledger/` | T7 audit ([`corpus-matrix.md`](archive/2026-07-28-debt-ledger/corpus-matrix.md)) + T4 `members_report_if_available` multithread tests |
| `performance/` | `archive/2026-07-28-performance/` | Q4 decided: verification stays unconditional, no skip knob |

Every other item on both reviews is fixed, accepted (bands aspirational, #191), or an
explicit KEEP with a recorded justification — see each review's `SUMMARY.md`.

## What is next

Ranked, from `backlog.md` and `PLAN.md`:

1. **Release bundle** (`PLAN.md` item 6) — the critical path to `0.2.0`. Landed since:
   the free-threading support statement and migration guide (`docs/support-matrix.md`,
   `docs/migrating.md`, #206) and the PyPI metadata (#207). **Remaining:** drop the
   `0.2.0.dev0` suffix when cutting the tag, and the repo-cutover leftovers
   (`docs/internal/release-repo-cutover.md`: discovery metadata, Pages settings).
2. **Docs full review** (in flight above) — the doc sweep, scoped as an information
   architecture problem rather than a content edit. Best done **before** more releases
   ship more permanent URLs.
3. **Topic 8** — documentation *content* (accuracy vs the code, then gaps, then
   quality). Separate from the IA review by design: that one decides where pages live,
   this one whether they are right. Starts from the `observations.md` the IA audit
   produces as a byproduct.
4. **Topic 6** — decode-engine performance (`backlog.md`); unblocked since #137.
5. **Topic 7** — outside-in adoption capstone. Run **last**: it judges the finished
   library, and items 1–4 are exactly the gaps it would otherwise re-find. The docs
   reviews deliberately hand persuasion/adoption findings to it rather than acting on
   them.

## Carried forward from the archived reviews

| Item | Where it lives now |
|------|--------------------|
| ~~Corpus rows unpinned in CI (ambient `7z` CLI)~~ | **Closed 2026-07-29** — `p7zip-full` on the Linux CI legs; see `archive/2026-07-28-debt-ledger/corpus-matrix.md` residual 1 |
| DD5–DD12, T5/T6, N1 (`pyppmd`), DD6 salvage | `archive/2026-07-28-debt-ledger/` — explicit KEEPs |
| P8/P9, L4/L5 listing/accelerator follow-ups | `archive/2026-07-28-performance/` |
| CLI `--json` / `--raw` | `IDEAS.md` (DD7/DD8) |

## Notes

- Private vulnerability reporting is **enabled** on `davitf/archivey`; see root
  `SECURITY.md`.
