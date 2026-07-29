# Brief — documentation full review (audience separation + information architecture)

Commissioned 2026-07-29 against `main` @ `403e7ff`. Prompted by the observation that
the README had accumulated links for four different audiences, and that the published
docs site carries far more maintainer material than user material.

## The ask

The repo's prose has grown organically to ~8,300 lines across four homes with no
enforced rule for what goes where. Produce an **information architecture** that
separates, cleanly and durably:

1. **User documentation** — someone using the library.
2. **Contributor documentation** — someone changing the code.
3. **Design record** — the normative "what is true" and the "why" behind it.
4. **History and evidence** — investigations, superseded prose, review and change logs.

Deliverable: an audit assigning **every** prose file to an audience and a target home,
a proposed structure, the decisions the maintainer must make, and a migration plan that
does not break published links.

This review is **analysis-first**: propose and get decisions before moving anything
(see Process below).

## Why now

`0.2.0` is close, and two things are about to freeze:

- **Published URLs.** The docs site is live, and `0.2.0.dev0` is on TestPyPI with a
  README pointing at specific `davitf.github.io/archivey/<page>/` URLs. **PyPI release
  metadata is immutable** — the README of a published release can never be edited. Every
  URL shipped in a release must keep resolving, forever. Reorganise before more releases
  ship more URLs, not after.
- **First impressions.** The PyPI page and the docs site are the front door for the
  adoption question Topic 7 (`backlog.md`) will judge. Structure is cheap to change now.

## The starting inventory (measured at `403e7ff`)

| Home | Files | Lines | Published on the site? |
|---|---:|---:|---|
| `docs/*.md` (user guide) | 11 | 1,482 | yes |
| `docs/internal/` | 12 | 3,968 | yes (6 of them **not in nav**) |
| `docs/grab-bag/` | 6 | 2,831 | yes, declared non-normative |
| Root `*.md` | 13 | ~1,900 | no |
| `docs/decisions/` | 15 | — | yes (1 not in nav) |
| `openspec/specs/` | 24 | — | no |
| `openspec/changes/archive/` | 60 dirs | — | no |
| `review/archive/` | 10 dirs | — | no |

The headline number: **the published site is roughly 82% non-user content** (6,799 of
8,281 published lines sit under `internal/` + `grab-bag/`). A user searching the docs for
"PPMd" lands in a 695-line upstream investigation report.

## Known problems to fold in (do not re-derive)

- **`docs/internal/` conflates three kinds of thing**: live normative registers
  (`threat-model.md`, `open-issues.md`, `known-issues.md`), process runbooks
  (`release-checklist.md`, `release-repo-cutover.md`), and finished investigations
  (`ppmd-*` ×3 at 1,668 lines, `pyppmd-upstream-report.md`,
  `rapidgzip-upstream-report.md`). These have different lifetimes and different readers.
- **`docs/grab-bag/` is published but explicitly non-normative** (2,831 lines of
  historical `SPEC`/`ARCHITECTURE`/`COMPARISON`/`ASYNC`). Its own index says "triage
  later" — this is that triage.
- **Six pages are built and reachable by URL but absent from the nav**
  (`internal/ppmd-*` ×3, `internal/pyppmd-upstream-report`,
  `internal/rapidgzip-upstream-report`, `decisions/0014-*`). Published-but-unlisted is
  already-existing drift, and the `--strict` build does not fail on it.
- **Four root tombstone stubs** (`ARCHITECTURE.md`, `ASYNC.md`, `COMPARISON.md`,
  `SPEC.md`) are 5–7 line "moved to…" pointers. They are precedent for a redirect
  pattern, and also clutter — decide which.
- **The repo root mixes every audience**: `README` (user), `CONTRIBUTING`/`AGENTS`/
  `CLAUDE` (contributor), `VISION`/`PLAN`/`IDEAS` (product direction),
  `CHANGELOG`/`SECURITY` (release), plus the four tombstones.
- **History lives in three parallel archives with three conventions**: `review/archive/`
  (dated dirs, `README.md` lifecycle), `openspec/changes/archive/` (dated dirs, OpenSpec
  lifecycle), `docs/grab-bag/` (undated, "historical"). A reader looking for "why did we
  decide X" must know which to search.
- **`AGENTS.md` (83 lines) and `CLAUDE.md` (117 lines) overlap**; `AGENTS.md` opens by
  deferring to `CLAUDE.md`. Decide whether one is canonical.
- **`known-issues.md` is user-relevant** (709 lines of real behaviour users hit) but
  filed under `internal/`. This is the clearest example of the audience question.

## The organising question

Suggested decision rule for the reviewer to test and refine — two axes, four quadrants:

| | **Current / normative** | **Historical / evidence** |
|---|---|---|
| **User** | published docs site | `CHANGELOG.md` |
| **Maintainer** | contributor guide, `openspec/specs/`, live registers, ADRs | investigations, superseded prose, `review/` + `changes/` archives |

Everything should land in exactly one quadrant, and **the published site should be the
"User + current" quadrant only** — that is the hypothesis to test, not a conclusion.
Contributor and design material stays in-repo (or in a clearly separated, unindexed
section) rather than interleaved with the user guide.

Note the deliberate tension to resolve rather than paper over: some material is genuinely
dual-audience. `threat-model.md` is a normative design record *and* the honest
security-posture statement an evaluating user wants. `known-issues.md` is maintainer
triage *and* user-facing gotchas. The reviewer should propose a rule (split the doc?
publish a user-facing summary that links the internal register?) rather than filing them
arbitrarily.

## Scope

- Assign **every** prose file (root, `docs/**`, `review/**`, `openspec/**`, `.claude/**`)
  to an audience and a target home. An explicit "delete" or "keep where it is" is a valid
  assignment; silence is not.
- Propose the target tree, the nav, and the rule that decides where a *new* doc goes.
- Identify duplication and contradiction across homes (the same thing explained twice,
  differently) — this is where drift hides.
- Check the user guide for **gaps** as well as excess: what would an adopting engineer
  look for and not find? (Coordinate with, do not pre-empt, Topic 7.)
- Propose the migration mechanics, including the redirect strategy (below).
- Propose the guardrail that keeps the structure from re-rotting.

## Out of scope

- Rewriting page *content* for quality/tone. Note it, do not do it — that is a separate
  pass (Topic 8, below), and mixing a content rewrite into a file-move review makes both
  unreviewable: a `git mv`-only diff can be verified by inspection, a move-plus-rewrite
  diff cannot.
  **But do capture what you notice.** The audit reads every file anyway, so record
  content observations in an `observations.md` for the content pass to start from —
  inaccuracies, duplication, pages that are really three pages, pages nobody should
  read. Recording them is free; acting on them is out of scope.
- Re-litigating decided ADRs or spec contracts.
- Topic 7 (outside-in adoption) — that judges whether the docs *persuade*; this one
  decides where they *live*. Findings that belong to Topic 7 should be handed to it.

## Sequencing: the content pass comes after, with two exceptions

Content review is **Topic 8** in `backlog.md` and runs after this one. The reasons are
practical, not aesthetic: polishing prose that is about to be merged or deleted is wasted
work, and a reviewer cannot judge whether a page is *complete* until it is clear what that
page is now responsible for.

Two kinds of content decision are genuinely structural and must be made **here**, not
deferred — deferring them would make the migration wrong rather than merely unpolished:

- **Merge / split / delete.** "These two pages say the same thing differently" and "this
  page is really three pages" are filing decisions. A page slated for deletion should not
  be moved first.
- **Dual-audience splits.** If `threat-model.md` or `known-issues.md` needs to become a
  user-facing page plus an internal register, that is a content operation that determines
  where each half lives.

Everything else — accuracy against the code, gaps, examples, tone, length — waits.

## Hard constraints

- **Published URLs must not 404.** Any page reachable today at
  `https://davitf.github.io/archivey/<path>/` that moves needs a redirect left behind
  (`mkdocs-redirects` is the usual mechanism, and would be a new docs-group dependency —
  evaluate it). URLs shipped in a *released* README (`0.2.0.dev0` onward) can never be
  fixed at the source and must be treated as permanent.
- **`mkdocs build --strict` must stay green**, and CI's docs job must keep passing.
- The `review/` and `openspec/` lifecycles are working and deliberate — propose changes
  to them only with a specific reason, not for symmetry.
- `openspec/specs/` is authoritative. Where prose and spec disagree, **pause and ask**
  (`CLAUDE.md`) rather than editing either to match.

## Bias control: the independent code-derived outline — **delivered**

> **Result (2026-07-29, #208).** The pass ran and its output is in
> [`independent/`](independent/). It produced the strongest signal the exercise could
> have produced: a **quantified proportional disagreement**, arrived at without ever
> seeing our docs.
>
> | Topic | Our guide | Its proposal |
> |---|---:|---:|
> | Safe extraction | **6.3%** (93 lines) | **~25%** |
> | Access modes / streaming / cost | **10.4%** (154 lines) | **~20%** |
> | Per-format notes | 12.1% | ~12% (agreement — ignore) |
> | `usage.md` (our largest page, 18.2%) | — | argues *against* a generic page |
>
> Its closing line — "if the real guide buries safe extraction and access modes behind a
> generic 'Usage' page, that is the failure mode this outline is arguing against" —
> describes our actual structure, which it could not see. Treat that as the finding, not
> as flattery: the guide under-weights the two pages that carry the safety and cost
> claims `VISION.md` is built on.
>
> **This is a phase-1 finding, not a Topic 8 one.** "Safe extraction should be ~4× its
> current size and `usage.md` should be split" is a page-shape decision, which the
> Sequencing section below already reserves to this review.
>
> Also delivered: `must-explain.md` (29 cited behaviours not inferable from signatures),
> `rationale-gaps.md` (32 places where the code shows *what* but not *why*), plus the
> API surface and two sample pages. Two caveats when using them:
>
> - **It could not see intent, and it over-reports because of it.** Verified example:
>   it flagged the ISO `pycdlib` monkeypatch as "surprising if documented nowhere", but
>   `iso_reader.py:20-30` documents it thoroughly — what is missing is only the
>   *user-facing* surfacing. Check each item against the code before acting.
> - **Agreements are weak evidence** (shared model priors), as this brief predicted.
>   The `formats` match at ~12% proves little. The disagreements above are the payload.
>
> Code-shaped findings are filtered into
> [`code-self-documentation.md`](code-self-documentation.md) rather than actioned from
> the raw output — see that file for why pointing an agent at these artifacts to "fix
> the code" would re-litigate settled design.

## Bias control: the independent code-derived outline

Everything above anchors you. Before phase 1 publishes, a separate agent derives a
documentation outline from **`src/` and `tests/` only** — no `docs/`, no `review/`, not
even this brief — and argues adversarially that the current structure is wrong. See
[`independent-brief.md`](independent-brief.md).

Its output is an **input to be diffed, not a proposal to adopt**. Triage it three ways:
we have it (weak signal); we lack it (the blind spot this is for); we deliberately don't
(record *why* — that rationale currently exists only in the maintainer's head).

Weight its **disagreements** heavily and its agreements lightly: isolating context removes
anchoring but not shared model priors, so convergence proves less than it appears to.

## Suggested process

A docs reorg differs from other reviews: it produces a **migration**, not just findings.
Recommended four phases, with a decision gate between the first two.

1. **Audit (this review).** Inventory + audience assignment + proposed tree + gap list.
   Deliverable per `review/README.md`: `SUMMARY.md`, theme files, `QUESTIONS.md`, and an
   `inventory.md` table (file → audience → current home → proposed home → rationale).
   **No files move in this phase.** The audit must be reviewable on its own.
2. **Decide.** Maintainer answers `QUESTIONS.md`: the taxonomy, what the site publishes,
   what is deleted vs archived, the dual-audience cases, `AGENTS`/`CLAUDE`. These are
   product/ownership calls, not reviewer calls.
3. **Execute** as one or more OpenSpec changes, kept **mechanical**: `git mv` + redirects
   + nav + link fixes, no content edits, so the diff is verifiable by inspection. Split
   by quadrant if it gets large — a reviewable series beats one 200-file commit.
4. **Guardrail.** Make the structure self-enforcing, or it re-rots within two months:
   a nav-completeness check (fail on published-but-unlisted, which would have caught the
   six pages above), a link checker in CI (internal links *and* the absolute URLs the
   README now ships), and a short "where does a new doc go?" rule in `CONTRIBUTING.md`
   pointing at the decision table.

Phase 4 is the part most likely to be skipped and the part that determines whether this
review is worth doing twice.

## Conventions

Inherits `review/README.md`. Analysis-only in phase 1; cite paths and line counts as of
`main` @ `403e7ff`. Where a proposal depends on a fact that was not verified (e.g.
whether a page is linked from outside the repo), say so rather than assuming.

Deliverable shape: `SUMMARY.md` (headline + proposed tree + top findings table),
`inventory.md` (the full file-by-file assignment), theme files as useful,
`QUESTIONS.md` (maintainer decisions), and a "what is actually fine" section — the
`review/` and `openspec/` lifecycles, the ADR log, and the user guide's core pages are
working and should not be churned for tidiness.
