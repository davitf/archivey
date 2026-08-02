# Documentation IA review — phase 1 (audit)

Brief: [`brief.md`](brief.md). Commissioning prompt: [`phase-1-prompt.md`](phase-1-prompt.md).
Measured at `4f154b9` (`main` @ `ce674bf` plus this review's two prompt commits).
The `docs/` tree is byte-identical to the brief's `403e7ff` baseline, so every
figure here is directly comparable to it. **Analysis only — no file has moved and
no page's content has been edited.**

| Deliverable | What it is |
|---|---|
| **`SUMMARY.md`** | this file — headline, tree, findings, what is fine |
| [`inventory.md`](inventory.md) | all 549 prose files → audience, current home, target home, rationale. The migration worklist. |
| [`QUESTIONS.md`](QUESTIONS.md) | the nine decisions that are yours, with a recommendation each |
| [`DECISIONS.md`](DECISIONS.md) | **the answers so far** — Q1/Q2 (revised)/D3 |
| [`target-tree.md`](target-tree.md) | the tree, the nav, the "where does a new doc go?" rule, the guardrails |
| [`page-shape.md`](page-shape.md) | the merge/split/delete decisions the brief reserves to this phase |
| [`observations.md`](observations.md) | 13 content problems recorded for Topic 8, not acted on |

---

## Headline

**The published site is 9,316 lines, of which 6,799 (73%) were written for
maintainers.** Excluding the ADR log — as the brief counts it — the figure is 82%.
The three PPMd investigation files alone are 1,668 lines, 20% of the site, none of
them in the navigation.

The inverse is the finding that matters more. The bias-control pass, which never
saw our docs, argued that safe extraction should be ~25% of a guide. Ours is
**6.3% — 93 lines**, the thinnest page on the site, carrying `VISION.md`'s
load-bearing claim #1 and backed by the largest spec in the tree (809 lines). It is
outweighed by `usage.md` (18.2%), `formats.md` (12.1%) and `migrating.md` (11.7%).

So the site is not merely mis-filed. **It is loudest where a maintainer needs it
and quietest where a user's mistake is unrecoverable.** Both halves are fixable
now, and the fix is mostly `git mv` plus four page splits.

### Decided (2026-07-29) — the tree is unblocked

The blocking question was that unpublishing `docs/internal/` **contradicts
`openspec/specs/documentation/spec.md`**, which requires the MkDocs site to present
internal/grab-bag as "clearly secondary" — i.e. present. Per `CLAUDE.md` that was
paused and surfaced rather than resolved. The maintainer has now answered
([`DECISIONS.md`](DECISIONS.md)):

- **D1 — unpublish.** The site becomes user-facing only. The spec deltas
  (`documentation`, and both specs naming `docs/internal/library-analysis.md`
  verbatim) are now required phase-3 work.
- **D2 — curated depth stays published; raw ADRs do not.** One new page,
  `docs/how-it-works.md`, carries the behind-the-scenes overview *and* a short
  decisions summary. The ADR files move to `dev-docs/decisions/` (D1 invariant:
  everything under `docs/` is published). User-page ADR links are inlined then
  dropped unless the ADR still has uninlinable end-user depth (then GitHub per
  D3). Writing `how-it-works.md` is Topic 8; phase 3 creates the slot and moves
  the ADR tree.
- **D3 — a new rule.** Published pages **must not link into unpublished docs**.
  Where the extra context is worth keeping, the link becomes an absolute
  `github.com/davitf/archivey/blob/main/…` URL. Of the nine such links today, 4 are
  removed, 5 become GitHub URLs, and `index.md`'s "For contributors" block is
  rewritten.

**Q3–Q9 remain open** — all page-level, none blocking the tree.

---

## Target tree

Full version with nav and rationale in [`target-tree.md`](target-tree.md).

```
README  CHANGELOG  SECURITY  CONTRIBUTING  AGENTS  VISION  PLAN  IDEAS
   (the four "moved to…" stubs deleted; CLAUDE.md merged into AGENTS.md)

docs/           ── PUBLISHED. User + current, and nothing else. ──
  index · install* · reading* · philosophy · gotchas(index) · safe-extraction(3×)
  access-and-cost · formats · errors-and-diagnostics* · migrating
  support-matrix · cli* · how-it-works† · api · acknowledgements
                            (* new, split out of usage.md   † new, D2 — includes
                              decisions summary; raw ADRs → `dev-docs/decisions/`)

dev-docs/       ── NOT published. Maintainer + current. ──
  index · threat-model(register) · open-issues · known-issues · library-analysis
  release-checklist · release-repo-cutover · decisions/ (raw ADR log)
  investigations/   finished evidence: ppmd ×3, pyppmd, rapidgzip, parallel-reader
  history/          superseded prose: SPEC · ARCHITECTURE · COMPARISON · ASYNC

review/  openspec/  .claude/  .cursor/     unchanged
```

**The invariant that makes it stick:** *everything under `docs/` is published, and
nothing else is under `docs/`.* That is a one-line CI check with no exception list
— which is the difference between this structure and the current one, where six
pages drifted out of the nav while the strict build stayed green.

---

## Top findings

| # | Finding | Severity | Where | Status |
|---|---|---|---|---|
| **F1** | The site is 73% maintainer material (82% excluding ADRs). A user searching "PPMd" lands in a 695-line upstream investigation. | **High** | `docs/internal/` 3,731 L, `docs/grab-bag/` 3,068 L | **Decided (D1): unpublish → `dev-docs/`.** Spec deltas to `documentation` + `packaging-and-extras` are phase-3 work |
| **F2** | `safe-extraction.md` is 93 lines (6.3%) — the thinnest page on the site — for `VISION.md` claim #1 and an 809-line spec. The material to triple it already exists, filed in four other places. | **High** | `docs/safe-extraction.md` | Proposed: absorb `gotchas.md` §Extraction (24 L), `threat-model.md` lines 26–58 (33 L), `SECURITY.md` lines 68–89 (22 L). [`page-shape.md`](page-shape.md) §1 |
| **F3** | `usage.md` (270 L, 18.2%) is five pages. The CLI — a 272-line spec, its own archived product review, "a wedge" in VISION — is 49 lines at its bottom with **no nav entry**. | **High** | `docs/usage.md` | Proposed: split into `install` / `reading` / `errors-and-diagnostics` / `cli`. [`page-shape.md`](page-shape.md) §2 |
| **F4** | The same facts are written 3–4× across `gotchas`/`costs`/`formats`/`internal`, **and have already drifted**: the rapidgzip gzip-truncation caveat exists four times, two of them narrower than the authoritative spec. | **High** | `formats.md:132`, `open-issues.md:132` vs `seekable-decompressor-streams/spec.md:125` | Recorded [`observations.md`](observations.md) O-2 (fix = Topic 8); the *structural* fix is Q3 |
| **F5** | Six pages are published, URL-reachable and search-indexed but absent from the nav — 1,846 lines. `mkdocs build --strict` prints them and exits **0**. | **Medium** | `decisions/0014`, `internal/ppmd-*` ×3, `internal/*-upstream-report` ×2 | Verified by running the build. Phase-4 guardrail #1 is a non-empty check on that exact output line |
| **F6** | `AGENTS.md` states the CLI is "planned, not implemented" and that "7z and RAR readers are not implemented yet". Both shipped. | **Medium** | `AGENTS.md:11-16` | Recorded [`observations.md`](observations.md) O-1. The strongest argument for Q5: the non-canonical guide is the one that rotted |
| **F7** | ADR 0014 is 615 lines — 59% of the ADR corpus, ~25× the median — and contains an `## Open questions` section while marked `Status: accepted`. It is also the ADR missing from the nav. | **Medium** | `docs/decisions/0014-*.md` | Proposed: split three ways. Q4 |
| **F8** | `threat-model.md` is two documents: an enforced-guarantees statement users want, and an O1–O8/C1–C4 maintainer register. `known-issues.md`, despite the same framing, is **not** — its user-relevant 5% is already summarised elsewhere. | **Medium** | `internal/threat-model.md` 320 L, `internal/known-issues.md` 709 L | The two dual-audience cases resolve **differently**: split one, move the other whole. Q7 |
| **F9** | Four root files are 5–7 line "moved to…" stubs. History lives in three parallel archives with three conventions. | **Low** | `ARCHITECTURE`/`ASYNC`/`COMPARISON`/`SPEC`.md | Proposed: delete the stubs (Q8); consolidate `grab-bag` under `dev-docs/history/` so history has two homes (in-repo prose, and the two dated review/change lifecycles) rather than three |
| **F10** | A published user page links the pre-rename repository (`davitf/archivey-2`). | **Low** | `docs/costs.md:17` | Recorded [`observations.md`](observations.md) O-4. The rename runbook listed "fix references" as a step; this one escaped it |

---

## Gaps in the user guide (as well as excess)

Coordinated with, not pre-empting, Topic 7 — which judges whether the docs
*persuade*; these are things a reader looks for and does not find.

| Gap | Evidence |
|---|---|
| **No install page.** "I `pip install`ed it, RAR didn't work, it's broken" is the independent pass's #1 predicted failure. Install is 8 lines atop `usage.md`; the format × extra × external-tool answer is spread across three pages; `format_availability()` exists in the API and no page is built around it. | `usage.md:3-12`, `formats.md:8-24`, `acknowledgements.md:57-73`, `support-matrix.md:60-80` |
| **No CLI page.** No nav entry at all. | `usage.md:215-262` |
| **Diagnostics have no narrative home.** Two lines in `safe-extraction.md` plus a bare mkdocstrings symbol list. The capability has its own 181-line spec. | `safe-extraction.md:90-93`, `api.md:38-51` |
| **Trust boundaries are only in an internal page.** The one place the three-layer symlink defence and atomic-write semantics are written is `internal/threat-model.md`, which `safe-extraction.md:28` links *out* to. | F2 / Q7 |
| **Caller hardening guidance is in `SECURITY.md`, not the guide.** | [`observations.md`](observations.md) O-7 |

---

## What is actually fine — do not churn it

Named explicitly so the migration does not sweep them up for symmetry.

- **The `review/` lifecycle.** In-flight at top level, dated dirs in `archive/`,
  `README.md` defining it, `STATUS.md` snapshotting it, `backlog.md` holding what
  is deferred. 87 files, one convention, and it visibly works — this review was
  commissioned, briefed, bias-controlled and delivered through it. **No changes.**
- **The `openspec/` lifecycle.** 24 authoritative specs, 3 in-flight changes, 60
  archived ones, `project.md` as cross-cutting context, schemas for authoring.
  **No changes** beyond the two deltas Q1 forces if it is approved — and those are
  a *consequence* of a decision, not a criticism of the lifecycle.
- **The ADR log**, ADR 0014 aside. 13 records of 21–105 lines, a maintained index —
  the *lifecycle* is fine and stays. Under revised D2 they move to
  `dev-docs/decisions/`; the published site carries a short decisions summary on
  `how-it-works.md` instead of the raw corpus.
- **The core user pages.** `philosophy`, `migrating`, `formats`, `support-matrix`,
  `acknowledgements` are good pages doing one job each. `support-matrix.md` in
  particular is unusually honest — it scopes every claim to the CI job that proves
  it. **No structural change to any of them.**
- **The `VISION` / `philosophy` split**, and the `VISION` / `PLAN` / `IDEAS` trio
  at the repo root. The maintainer-vision-vs-user-distill split is deliberate and
  holds; the root trio is a legible convention, not clutter (Q6).
- **`.claude/` and `.cursor/`.** Executable configuration that happens to be
  Markdown, addressed by tools at literal paths. Filing them as "docs" would invite
  a move that breaks the tools.
- **`mkdocs.yml` itself.** The mkdocstrings/Griffe setup, the fieldz and enum
  extensions, the strict CI build. The changes proposed are nav entries, not
  machinery.

---

## What this review deliberately did not do

- **No content rewriting.** Thirteen accuracy and duplication problems are recorded
  in [`observations.md`](observations.md) and handed to Topic 8. Mixing a rewrite
  into a file-move review makes both unreviewable: a `git mv`-only diff can be
  verified by inspection, a move-plus-rewrite diff cannot.
- **No spec edits.** Where prose and `openspec/specs/` disagree, this review
  surfaces it (Q1) rather than editing either to match.
- **No redirects, and no `mkdocs-redirects` dependency.** URL churn is free until
  the `0.2.0` tag; that budget was spent on the structure instead. The one thing
  that does freeze is the five absolute `davitf.github.io` URLs in `README.md` —
  confirming those against the final nav is the last step before tagging.
- **No adoption judgement.** Whether the resulting docs *persuade* is Topic 7's
  call; findings that belong to it are marked as handed over in
  [`page-shape.md`](page-shape.md).

---

## Next

1. Answer the rest of [`QUESTIONS.md`](QUESTIONS.md) — Q3–Q9. None blocks the tree
   now that Q1 is decided; Q5, Q6 and Q8 are independent one-commit calls.
2. Phase 3: execute as OpenSpec changes, kept mechanical — the nine-commit
   sequence is in [`inventory.md`](inventory.md) §Migration mechanics. Commits 1–3
   are `git mv` + link repoints; only commits 5–8 touch prose, one page each.
3. Phase 4: the three guardrails in [`target-tree.md`](target-tree.md). The brief
   is right that this is the part most likely to be skipped and the part that
   decides whether this review gets done twice — F5 is what its absence looks like.

**Baseline:** `uv run --group docs mkdocs build --strict` is green at `ce674bf`
(and prints the six unlisted pages from F5). No source files were changed, so the
three-config test matrix does not apply.
