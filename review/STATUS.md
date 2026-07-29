# In-flight review status (2026-07-28)

**No reviews are in flight.** `debt-ledger/` and `performance/` were archived on
2026-07-28 after the last two ledger items (**T7** corpus-matrix audit, **T4**
half-test) landed and **performance Q4** was decided. This file stays as the entry
point for the next round.

## What closed the round

| Review | Archived as | Closing work |
|--------|-------------|--------------|
| `debt-ledger/` | `archive/2026-07-28-debt-ledger/` | T7 audit ([`corpus-matrix.md`](archive/2026-07-28-debt-ledger/corpus-matrix.md)) + T4 `members_report_if_available` multithread tests |
| `performance/` | `archive/2026-07-28-performance/` | Q4 decided: verification stays unconditional, no skip knob |

Every other item on both reviews is fixed, accepted (bands aspirational, #191), or an
explicit KEEP with a recorded justification — see each review's `SUMMARY.md`.

## What is next (not review work)

Ranked, from `backlog.md` and `PLAN.md`:

1. **Release bundle** (`PLAN.md` item 6) — the actual critical path to `0.2.0`, and
   the one thing no review ever tracked: packaging finalize (`version` is still
   `0.2.0.dev0`), the **explicit free-threading support statement** (today it lives
   only in `docs/internal/threat-model.md` C4 and `AGENTS.md`, nothing user-facing),
   and the **migration guide** (`zipfile`/`tarfile`/`shutil.unpack_archive`/`patool`
   → archivey) + doc sweep.
2. **Topic 6** — decode-engine performance (`backlog.md`); unblocked since #137.
3. **Topic 7** — outside-in adoption capstone. Run **last**: it judges the finished
   library, and items 1–2 are exactly the gaps it would otherwise re-find.

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
