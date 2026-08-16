# In-flight review status (2026-08-15)

## In flight

| Review | State |
|---|---|
| [`problem-catalogue/`](problem-catalogue/brief.md) — the **problem catalogue** (Topic 10) | **Commissioned 2026-08-15** at `d4668c3`. Not a review: extraction and normalization over ~180 documents that already state a problem (72 change proposals, 57 `design.md`, 17 ADRs, 11 review summaries, 8 investigations, threat model, `known-issues`, `library-analysis`, `dev-docs/history/`). One entry per problem, N sources, stated **solution-neutrally** — the decision that resolved it is a separate strippable field, because the second consumer is a fresh-design comparison run against the problems alone. Parallel with Topic 8 on disjoint sources; takes the code-comment residue from its capability workers. |
| [`docs-content/`](docs-content/brief.md) — documentation **content** (Topic 8) | **Commissioned 2026-08-15** at `d4668c3`, once the library churn the prose was waiting on had landed (`#225`, `#232`, `#233`–`#236`). Four passes, run in sequence rather than triaged — accuracy vs the code → gaps → register (O-16/O-17) → quality — with no budget or target date; the brief's §Definition of done says what completion means. Starts from `docs/observations.md` and `docs/outline.md`; the outline's "~455 lines outstanding" needs re-tallying first (~226 lines landed opportunistically since). Writes prose directly — a library defect found becomes a separate fix PR, never an edit inside a docs PR. |
| [`docs/`](docs/brief.md) — documentation full review | Brief written 2026-07-29. Four-phase process (audit → decide → migrate → guardrail). **Phase 1 (audit) delivered** — [`SUMMARY.md`](docs/SUMMARY.md), [`inventory.md`](docs/inventory.md) (all 549 prose files assigned), [`QUESTIONS.md`](docs/QUESTIONS.md), [`observations.md`](docs/observations.md). Headline: the site is 73% maintainer material, and `safe-extraction.md` is its thinnest page. **Phase 2 (decide) complete** — D1–D11 in [`DECISIONS.md`](docs/DECISIONS.md), no questions open. **Phase 3 (migrate) done** — `docs-ia-unpublish-maintainer-tree` landed in #221 and archived in #222; `docs-ia-split-user-guide` is implemented in #223. Phase 4's guardrails shipped with the first change. Bias control pass delivered earlier (#208, `docs/independent/`); code-shaped findings filtered into [`code-self-documentation.md`](docs/code-self-documentation.md). Writing the guide kept finding *library* defects (#225); that class was absorbed by Topic 9, now archived. **The prose half is now [`docs-content/`](docs-content/brief.md)** (row above); this review stays open only for `how-it-works.md`, which D2 assigns there. |

### Phase 3, split in two

The move-only half and the prose half are separate changes for the same reason the
IA review and Topic 8 are separate: a rename-only diff is verifiable by inspection
and a move-plus-rewrite diff is not.

| Change | Owns |
|---|---|
| `docs-ia-unpublish-maintainer-tree` — **landed #221, archived #222** | The moves to `dev-docs/`, the four root stub deletions, ~90 reference repoints, the D2/D3 link resolution, and phase-4 guardrails 1–3 |
| `docs-ia-split-user-guide` — **implemented in #223, archived in #224** | The page splits (`usage.md` ×5, ADR 0014 ×3, `threat-model.md` ×2, the `gotchas.md` shrink), the D4 Gotchas spec delta, the `documentation` delta for the `usage.md`-named listing requirement and the Gotchas-after clause, and the `AGENTS`/`CLAUDE` merge (D6). **Not** `how-it-works.md` — 100% new prose, so it belongs to whichever change writes it |

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
decided. Topic 9 (`simplicity-consistency`) was archived on **2026-08-15** after `#232`
paid W1–W9 and the Q13/O-23 follow-ons (`#233`–`#236`) landed.

## What closed

| Review | Archived as | Closing work |
|--------|-------------|--------------|
| `simplicity-consistency/` (Topic 9) | [`archive/2026-08-15-simplicity-consistency/`](archive/2026-08-15-simplicity-consistency/) | Findings #230/#231; W1–W9 in **#232** (six OpenSpec changes + ADRs 0015–0017); expansions **#233–#236**. Guardrails in `tests/test_review_simplicity_consistency.py`. **O2b/O2c** → `IDEAS.md`. |
| `debt-ledger/` | `archive/2026-07-28-debt-ledger/` | T7 audit ([`corpus-matrix.md`](archive/2026-07-28-debt-ledger/corpus-matrix.md)) + T4 `members_report_if_available` multithread tests |
| `performance/` | `archive/2026-07-28-performance/` | Q4 decided: verification stays unconditional, no skip knob |

Every other item on those reviews is fixed, accepted (bands aspirational, #191), or an
explicit KEEP / park with a recorded justification — see each review's `SUMMARY.md`.

## What is next

Ranked, from `backlog.md` and `PLAN.md`:

1. **Release bundle** (`PLAN.md` item 6) — the critical path to `0.2.0`. Landed since:
   the free-threading support statement and migration guide (`docs/support-matrix.md`,
   `docs/migrating.md`, #206) and the PyPI metadata (#207). **Remaining:** drop the
   `0.2.0.dev0` suffix when cutting the tag, and the repo-cutover leftovers
   (`dev-docs/release-repo-cutover.md`: discovery metadata, Pages settings).
2. **Topic 8 — documentation *content*** (`docs-content/`, commissioned above). Separate
   from the IA review by design: that one decides where pages live, this one whether
   they are right. Best finished **before** more releases ship more permanent URLs.
   Library defects found while writing prose become fix PRs (Topic 9's class is closed).
   The docs IA review stays in flight alongside it — its last deliverable,
   `how-it-works.md`, is D2's and therefore Topic 8's; both archive together.
3. **Topic 10 — the problem catalogue** (`problem-catalogue/`, commissioned above). Runs
   **in parallel with Topic 8**, not after it: the sources are disjoint, and Topic 8's
   capability workers supply its code-comment residue as a byproduct. It **may enrich**
   `how-it-works.md` and the 32 rationale gaps on the docs side — it does **not** gate
   them, since D2 already names a source for each of that page's six sections — and it
   feeds a fresh-design comparison later.
4. **Topic 6** — decode-engine performance (`backlog.md`); unblocked since #137.
   Absorbs parked stream-layering Q4 and Topic 9's solid-decoder-hold idea (O2b/O2c
   in `IDEAS.md`).
5. **Topic 7** — outside-in adoption capstone. Run **last**: it judges the finished
   library, and items 1–4 are exactly the gaps it would otherwise re-find. It is also a
   consumer of Topic 10 — "what problems does this library solve that a naive one does
   not?" is its question, and the catalogue is the evidence. The docs
   reviews deliberately hand persuasion/adoption findings to it rather than acting on
   them.

## Carried forward from the archived reviews

| Item | Where it lives now |
|------|--------------------|
| ~~Corpus rows unpinned in CI (ambient `7z` CLI)~~ | **Closed 2026-07-29** — `p7zip-full` on the Linux CI legs; see `archive/2026-07-28-debt-ledger/corpus-matrix.md` residual 1 |
| DD5–DD12, T5/T6, N1 (`pyppmd`), DD6 salvage | `archive/2026-07-28-debt-ledger/` — explicit KEEPs |
| P8/P9, L4/L5 listing/accelerator follow-ups | `archive/2026-07-28-performance/` |
| CLI `--json` / `--raw` | `dev-docs/IDEAS.md` (DD7/DD8) |
| Solid-block decoder hold across `open()` + concurrency (O2b/O2c) | `dev-docs/IDEAS.md` §Performance — from Topic 9 |

## Notes

- Private vulnerability reporting is **enabled** on `davitf/archivey`; see root
  `SECURITY.md`.
