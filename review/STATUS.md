# In-flight review status (2026-08-03)

## In flight

| Review | State |
|---|---|
| [`docs/`](docs/brief.md) — documentation full review | Brief written 2026-07-29. Four-phase process (audit → decide → migrate → guardrail). **Phase 1 (audit) delivered** — [`SUMMARY.md`](docs/SUMMARY.md), [`inventory.md`](docs/inventory.md) (all 549 prose files assigned), [`QUESTIONS.md`](docs/QUESTIONS.md), [`observations.md`](docs/observations.md). Headline: the site is 73% maintainer material, and `safe-extraction.md` is its thinnest page. **Phase 2 (decide) complete** — D1–D11 in [`DECISIONS.md`](docs/DECISIONS.md), no questions open. **Phase 3 (migrate) half done** — `docs-ia-unpublish-maintainer-tree` landed in #221 and archived in #222; the follow-up splits change is next. Bias control pass delivered earlier (#208, `docs/independent/`); code-shaped findings filtered into [`code-self-documentation.md`](docs/code-self-documentation.md). |

### Phase 3, split in two

The move-only half and the prose half are separate changes for the same reason the
IA review and Topic 8 are separate: a rename-only diff is verifiable by inspection
and a move-plus-rewrite diff is not.

| Change | Owns |
|---|---|
| `docs-ia-unpublish-maintainer-tree` — **landed #221, archived #222** | The moves to `dev-docs/`, the four root stub deletions, ~90 reference repoints, the D2/D3 link resolution, and phase-4 guardrails 1–3 |
| Follow-up (next, outline delivered) | The four page splits (`usage.md` ×4, ADR 0014 ×3, `threat-model.md` ×2, the `gotchas.md` shrink), `docs/how-it-works.md`, the D4 Gotchas spec delta, the `documentation` delta for the `usage.md`-named listing requirement, and the `AGENTS`/`CLAUDE` merge (D6) |

**The outline is delivered** — [`docs/outline.md`](docs/outline.md), written between
the two changes: all 15 published pages with purpose, reader question, sections in
order, explicit non-coverage, and `file:lines` sources. It is the worklist the splits
execute against and the one Topic 8 starts from. Headline findings:

- **The proportions land**, once the denominator is stated. Against the core teaching
  pages — the comparable denominator, since the independent pass's outline had no
  migration, platform or API page — safe extraction reaches 23.8% against its ~25%
  target, and access/cost 16.6% against ~20%.
- **Nine of the 29 must-explain behaviours are documented nowhere today**, and eight
  of those land on `reading.md` or `install.md` — the two pages that do not exist
  yet. That is the outline's own argument for splitting `usage.md` rather than
  polishing it.
- **~410 lines of new prose** are needed that no merge can supply, over half of it
  on `safe-extraction.md` and the new `errors-and-diagnostics.md`. That is Topic 8's
  floor, before the accuracy pass it was commissioned for.

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
   (`dev-docs/release-repo-cutover.md`: discovery metadata, Pages settings).
2. **Docs full review** (in flight above) — the doc sweep, scoped as an information
   architecture problem rather than a content edit. Best done **before** more releases
   ship more permanent URLs.
3. **Topic 8** — documentation *content* (accuracy vs the code, then gaps, then
   quality). Separate from the IA review by design: that one decides where pages live,
   this one whether they are right. Starts from the IA review's `observations.md`
   and `outline.md`, which between them name ~410 lines of prose that must be written
   and 15 recorded content problems (O-14 closed by #212).
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
| CLI `--json` / `--raw` | `dev-docs/IDEAS.md` (DD7/DD8) |

## Notes

- Private vulnerability reporting is **enabled** on `davitf/archivey`; see root
  `SECURITY.md`.
