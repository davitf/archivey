# In-flight review status (2026-08-06)

## In flight

| Review | State |
|---|---|
| [`docs/`](docs/brief.md) — documentation full review | Brief written 2026-07-29. Four-phase process (audit → decide → migrate → guardrail). **Phase 1 (audit) delivered** — [`SUMMARY.md`](docs/SUMMARY.md), [`inventory.md`](docs/inventory.md) (all 549 prose files assigned), [`QUESTIONS.md`](docs/QUESTIONS.md), [`observations.md`](docs/observations.md). Headline: the site is 73% maintainer material, and `safe-extraction.md` is its thinnest page. **Phase 2 (decide) complete** — D1–D11 in [`DECISIONS.md`](docs/DECISIONS.md), no questions open. **Phase 3 (migrate) done** — `docs-ia-unpublish-maintainer-tree` landed in #221 and archived in #222; `docs-ia-split-user-guide` is implemented in #223. Phase 4's guardrails shipped with the first change. Bias control pass delivered earlier (#208, `docs/independent/`); code-shaped findings filtered into [`code-self-documentation.md`](docs/code-self-documentation.md). Writing the guide keeps finding *library* defects (#225); those are out of Topic 8's scope and feed the new review below. |
| [`simplicity-consistency/`](simplicity-consistency/brief.md) — simplicity & consistency | **Findings delivered** against `main` @ `2792f9c` (post-#225/#227). Analysis-only: ranked pay list in [`QUESTIONS.md`](simplicity-consistency/QUESTIONS.md); matrix in [`parity-matrix.md`](simplicity-consistency/parity-matrix.md); evidence in `repro/` + `tests/test_guardrails.py`. Headline accidents: volumes `ValueError` (F1), silent `encoding=` (F2), ZIP underlying-close → CorruptionError (F3), Path-gated `compressed_size` (F6). No library behaviour changes until maintainer picks pay items. |

### Phase 3, split in two

The move-only half and the prose half are separate changes for the same reason the
IA review and Topic 8 are separate: a rename-only diff is verifiable by inspection
and a move-plus-rewrite diff is not.

| Change | Owns |
|---|---|
| `docs-ia-unpublish-maintainer-tree` — **landed #221, archived #222** | The moves to `dev-docs/`, the four root stub deletions, ~90 reference repoints, the D2/D3 link resolution, and phase-4 guardrails 1–3 |
| `docs-ia-split-user-guide` — **implemented, this PR** | The page splits (`usage.md` ×5, ADR 0014 ×3, `threat-model.md` ×2, the `gotchas.md` shrink), the D4 Gotchas spec delta, the `documentation` delta for the `usage.md`-named listing requirement and the Gotchas-after clause, and the `AGENTS`/`CLAUDE` merge (D6). **Not** `how-it-works.md` — 100% new prose, so it belongs to whichever change writes it |

**The outline is delivered** — [`docs/outline.md`](docs/outline.md), written between
the two changes: all 16 published pages with purpose, reader question, sections in
order, explicit non-coverage, and `file:lines` sources. It is the worklist the splits
execute against and the one Topic 8 starts from. Headline findings:

- **The proportions land**, once the denominator is stated. Against the core teaching
  pages — the comparable denominator, since the independent pass's outline had no
  migration, platform or API page — safe extraction reaches 22.3% against its ~25%
  target, and access/cost 18.3% against ~20%.
- **Nine of the 29 must-explain behaviours are documented nowhere today**, and eight
  of those land on the three pages that do not exist yet — `install.md`,
  `opening-and-listing.md`, `reading-members.md`. That is the outline's own argument
  for splitting `usage.md` rather than polishing it.
- **~495 lines of new prose** are needed that no merge can supply, over half of it
  on `extracting.md` and the new `errors-and-diagnostics.md`. That is Topic 8's
  floor, before the accuracy pass it was commissioned for. (~40 of them shipped with
  the splits change; see below.)

A structure review on #223 added four things the outline had missed: a 30-second
recipes block on Home (the independent pass's page 1, dropped), a config-at-a-glance
screen, the four D8 threat-model residual one-liners on Gotchas, and Home's Highlight
link repoints. Three page-shape questions it raised are **decided** in `outline.md`
§Decided: nav order stands, and the config screen is a section rather than a page.
The third — whether `reading.md` stays one page — was decided yes, then **reversed**
when the maintainer asked for a section-by-section tally: the page came to 268 lines,
`usage.md`'s own size, and divided 133/135 between "what's in this archive" and
"give me the bytes". It becomes `opening-and-listing.md` + `reading-members.md`, and
the dedupe recipe moves to `formats.md` beside the stored-digest matrix it depends
on. The nav goes to 16 entries.

**The splits change is implemented.** The guide is now 15 pages, each doing one job:
`usage.md` split five ways, `costs.md` → `access-and-cost.md`, `safe-extraction.md` →
`extracting.md` grown from 93 to 181, `gotchas.md` shrunk from 155 to 87 as a digest,
ADR 0014 and `threat-model.md` split, and `AGENTS.md` canonical with `CLAUDE.md` a 26-line
pointer.

**What ships thin, on purpose.** This change moved blocks; it wrote ~40 of the ~495
lines of new prose the outline identifies and left ~455 for Topic 8. `install.md` has
no `format_availability()` section, `errors-and-diagnostics.md` no diagnostics
narrative, `access-and-cost.md` no config screen, and `how-it-works.md` does not exist
— so the nav is 15 entries, not the outline's 16. `outline.md` is the specification
for the rest.

**A re-review caught four consistency problems this change had created**, all now
fixed: `index.md`'s recipes were promised by decision D-a but never written (the nav
order rests on them, so they shipped); `access-and-cost.md` still said accelerator
faults abort the process while the rewritten `gotchas.md` said they are contained;
the Gotchas nesting line pointed at a Limits section that never mentioned nesting; and
two Errors callouts were recorded as done before being written. Three of the four were
the same error — writing a record in the present tense ahead of the work it describes.

**Two later refinements**, both settled with the maintainer after the first pass and
recorded as `outline.md` D-d/D-e: `safe-extraction.md` became **`extracting.md`** (the
sibling form is verb-ing, and `philosophy.md` calls safety "a contract, not a marketing
flag" — a page asserting "safe" in its filename is the flag), and the **damage contract**
moved out of the two flow pages into `errors-and-diagnostics.md` under "When an archive
is damaged". The flow keeps the one-line honesty promise plus a link, and keeps the
`read(member.size)` asymmetry because that one is a footgun rather than depth.
`reading-members` 129 → 84, `opening-and-listing` 90 → 76, `errors-and-diagnostics`
43 → 140.

That reversed an argument I had made against it — that damage is "a VISION founding use
case". It is not: VISION's load-bearing claims are safe-by-default and memory-safe
parsing, and the founding use case is deduplicating messy backups.

**Two things the move surfaced that were not on anyone's list:** ~35 references to
`docs/internal/` and `docs/grab-bag/` survived *inside* `dev-docs/` because #221's
sed pass never covered that tree, and the ZIP UTF-8 bit-11 row existed only on
Gotchas — the spec delta's migration note had claimed `formats.md` already carried
it. Both fixed here.

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
2. **Docs full review** (in flight above) — IA largely done; remaining Topic 8 prose
   and accuracy. Best finished **before** more releases ship more permanent URLs.
3. **Simplicity & consistency** (in flight above) — behavioural uniformity and
   accidental complexity before the public API freezes. Can overlap Topic 8: docs
   writing feeds seeds; this review owns library/spec fixes.
4. **Topic 8** — documentation *content* (accuracy vs the code, then gaps, then
   quality). Separate from the IA review by design: that one decides where pages live,
   this one whether they are right. Starts from the IA review's `observations.md`
   and `outline.md`. Library defects found while writing prose go to
   `simplicity-consistency/` (or a fix PR), not into Topic 8's rewrite budget.
5. **Topic 6** — decode-engine performance (`backlog.md`); unblocked since #137.
6. **Topic 7** — outside-in adoption capstone. Run **last**: it judges the finished
   library, and items 1–5 are exactly the gaps it would otherwise re-find. The docs
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
