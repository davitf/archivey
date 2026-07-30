# Target tree, nav, and the rule that keeps it

Companion to [`inventory.md`](inventory.md). **Q1 and Q2 are decided** — the site
unpublishes maintainer material and keeps the ADR log ([`DECISIONS.md`](DECISIONS.md)
D1/D2), so the tree below is the agreed target, not a proposal. Q3–Q9 still affect
individual pages, marked where they do.

## The decision rule

The brief proposes two axes and four quadrants. Testing it against the real tree,
it holds — with one refinement (the ADR log) and one clarification (agent tooling
is not documentation).

| | **Current / normative** | **Historical / evidence** |
|---|---|---|
| **User** | `docs/` — the published site | `CHANGELOG.md` |
| **Maintainer** | `CONTRIBUTING.md`, `AGENTS.md`, `VISION`/`PLAN`/`IDEAS`, `openspec/specs/`, `dev-docs/` (live registers + runbooks) | `dev-docs/investigations/`, `dev-docs/history/`, `review/archive/`, `openspec/changes/archive/` |

**The invariant that makes it enforceable:**

> Everything under `docs/` is published and is for users.
> Nothing else under `docs/` exists.

That is a one-line CI check (`every docs/**/*.md appears in mkdocs.yml nav`) with
no exception list. The alternative — keeping maintainer material under `docs/` and
excluding it via `exclude_docs` — needs a second list that must stay in sync with
the first, which is precisely the drift that produced six published-but-unlisted
pages. The cheaper `exclude_docs` alternative was weighed and rejected in Q1.

**Refinement — `docs/decisions/` stays published** (decided, D2). Strictly it is
"Maintainer + current", so the pure hypothesis would unpublish it. An ADR answers
"why did you write your own 7z parser instead of wrapping `py7zr`?", which is an
*adoption* question, and Topic 7 will judge whether the docs answer it. The ADRs
are 21–105-line curated records with a maintained index — a different object from
a 695-line investigation. `docs/how-it-works.md` (new, D2) covers the other half of
"curated implementation detail for curious users". Both are named exceptions, not
holes in the rule.

**Clarification — `.claude/` and `.cursor/` are configuration.** They are Markdown
addressed by tools at literal paths. Filing them as "contributor docs" invites a
move that breaks the tools.

## Where a new doc goes

Four questions, in order. The first `yes` wins.

1. **Would someone who only *uses* the library need it?** → `docs/`, and add it to
   the nav in the same commit.
2. **Is it a load-bearing "why" that is decided and won't change?** → a new ADR in
   `docs/decisions/` (keep it ADR-shaped: Context / Decision / Consequences, tens
   of lines). If it needs an `## Open questions` section, it is not an ADR yet.
3. **Does a contributor need it to work on the code *today*?** → `dev-docs/` (live
   register or runbook).
4. **Is it finished evidence — an investigation, a superseded design, a lab
   notebook?** → `dev-docs/investigations/` (or `dev-docs/history/` for prose that
   a newer document replaced).

If it is a *review*, it belongs to the `review/` lifecycle. If it is a *proposed
behaviour change*, it belongs to `openspec/changes/`. Neither is affected by this
review.

### And the linking rule that goes with it (D3)

A published page **must not link into unpublished docs**. In order:

1. **Prefer no link.** If a published page needs a fact, the fact belongs on a
   published page — a link into maintainer material usually means it is filed in
   the wrong place.
2. **If the extra context is genuinely worth keeping**, link the file on GitHub:
   `https://github.com/davitf/archivey/blob/main/dev-docs/<file>.md` — the pattern
   `README.md:20-22` already uses.
3. **Never** a bare repo path in prose standing in for a link.

Nine such links exist today; [`DECISIONS.md`](DECISIONS.md) D3 resolves each one.

## Proposed tree

```
README.md                    user front door (absolute docs URLs; freezes at 0.2.0)
CHANGELOG.md  SECURITY.md    user + release
CONTRIBUTING.md  AGENTS.md   contributor  (AGENTS absorbs CLAUDE.md — Q5)
VISION.md  PLAN.md  IDEAS.md product direction
                             (the 4 "moved to…" stubs are deleted)

docs/                        ── PUBLISHED. User + current, and nothing else. ──
  index.md
  install.md                 NEW ← usage.md; + format × extra × external-tool table
  reading.md                 NEW ← usage.md; open/list/read/stream/detect/passwords
  philosophy.md
  gotchas.md                 shrunk to a link-per-bullet index (Q3)
  access-and-cost.md         ← costs.md, + the cost half of gotchas.md
  safe-extraction.md         grown ~3×: + extraction half of gotchas.md,
                               + user half of threat-model.md,
                               + SECURITY.md caller-hardening notes
  formats.md
  errors-and-diagnostics.md  NEW ← usage.md error section + diagnostics
  migrating.md
  support-matrix.md
  cli.md                     NEW ← usage.md (49 lines today, no nav entry)
  how-it-works.md            NEW — curated behind-the-scenes overview (D2)
  api.md
  acknowledgements.md
  decisions/                 15 ADRs (0014 split — Q4)

dev-docs/                    ── NOT published. Maintainer + current. ──
  index.md                   ← internal/index.md
  threat-model.md            gap register only (user half → safe-extraction.md)
  open-issues.md
  known-issues.md
  library-analysis.md        ← named verbatim by two specs; needs a delta (Q1)
  release-checklist.md
  release-repo-cutover.md
  investigations/            ── Maintainer + historical. Finished evidence. ──
    ppmd-native-investigation-brief.md
    ppmd-native-investigation-results.md
    ppmd-exit-after-green-exploration.md
    pyppmd-upstream-report.md
    rapidgzip-upstream-report.md
    parallel-reader.md       ← grab-bag/ (still cited from src/)
    adr-0014-investigation.md  ← the 585 lines split out of ADR 0014 (Q4)
  history/                   ── Superseded prose. Kept for ADR provenance. ──
    index.md  SPEC.md  ARCHITECTURE.md  COMPARISON.md  ASYNC.md

review/                      unchanged — the lifecycle works
openspec/                    unchanged — specs authoritative, changes lifecycle works
.claude/  .cursor/           unchanged — tool configuration
```

## Proposed nav

Ordered by what a reader needs first, which is also the order the independent
code-derived pass argued for. `Gotchas` stays immediately after basic usage as
`openspec/specs/documentation/spec.md:86` requires.

```yaml
nav:
  - Home: index.md
  - Install and extras: install.md
  - Reading archives: reading.md
  - Gotchas: gotchas.md
  - Safe extraction: safe-extraction.md
  - Access costs and pitfalls: access-and-cost.md
  - Formats and extras: formats.md
  - Errors and diagnostics: errors-and-diagnostics.md
  - Command line: cli.md
  - Migrating from zipfile/tarfile: migrating.md
  - Platforms and threading: support-matrix.md
  - Philosophy: philosophy.md
  - How it works: how-it-works.md
  - API reference: api.md
  - Acknowledgements: acknowledgements.md
  - Decisions:
      - Overview: decisions/index.md
      - … (14 ADRs, including 0014 — which is missing today)
```

The `Internal:` and `Grab-bag:` nav sections (13 entries) are deleted. Published
lines drop from **9,316** (all of `docs/**` today) to roughly **2,000** — the
1,482-line guide, near-neutral through the splits, plus `decisions/` at ~450 once
ADR 0014's investigation half moves out. That estimate is approximate: it assumes
no new prose, and Topic 8 will legitimately grow `safe-extraction.md` further. What
is not approximate is that every published line is then for a user.

`Philosophy` moves down from position 2. It is a good page, but a reader who has
not yet installed the library does not need the manifesto before the install
instructions; `README.md` already carries the pitch.

## Guardrails (phase 4)

The brief calls phase 4 "the part most likely to be skipped and the part that
determines whether this review is worth doing twice." Three checks, cheapest first:

1. **Nav completeness.** Fail CI when a file under `docs/` is not in
   `mkdocs.yml`'s nav. `mkdocs build` already *prints* this — today it emits six
   such lines and exits 0 (verified: `mkdocs build --strict` is green at
   `ce674bf` while listing `decisions/0014-*` and five `internal/*` pages). The
   check is: parse that INFO line, exit non-zero if non-empty. This is what would
   have caught all six.
2. **Link checking**, three halves now that D3 is decided: internal relative links
   (`mkdocs --strict` covers cross-references but not arbitrary relative paths);
   the **absolute** `davitf.github.io` URLs in `README.md`, which nothing checks
   today and which freeze at the tag; and the **`github.com/davitf/archivey/blob/main/`
   URLs that D3 introduces into `docs/**`** — those point at files this migration
   is itself moving, so they are exactly the class that rots silently. D3 raises
   this guardrail from precautionary to load-bearing.
3. **The placement rule** (above) in `CONTRIBUTING.md`, next to the existing
   "Working with the specs" section.

A fourth, if the maintainer wants the invariant to be real rather than
conventional: fail CI when a new `docs/**` file is added without a nav entry *in
the same commit*. That is (1) and costs nothing extra.
