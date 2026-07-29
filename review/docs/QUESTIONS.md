# Questions for the maintainer

Phase 2 of the docs IA review is "Decide" (`brief.md`). These are the calls that
are product/ownership decisions, not reviewer decisions. Each has a
recommendation; none is decided here.

> **Q1 and Q2 are answered** (2026-07-29) — see [`DECISIONS.md`](DECISIONS.md).
> The site is unpublishing `docs/internal/`; `docs/decisions/` stays; and a **new
> rule D3** applies: published pages must not link into unpublished docs, and where
> the context is worth keeping the link becomes an absolute GitHub URL. Q3–Q9 below
> are still open.

---

## Q1 — Does the published site stop carrying maintainer material? ✅ **ANSWERED: option A**

> *"we can unpublish. let's leave the published docs user-facing only"*

Recorded as [`DECISIONS.md`](DECISIONS.md) D1. The spec deltas below are now
required work, not a hypothetical cost. The rest of this section is kept as the
reasoning behind the decision.

The brief's hypothesis is that the site should be the "User + current" quadrant
only, which means unpublishing `docs/internal/` (12 files, 3,731 lines) and
`docs/grab-bag/` (6 files, 3,068 lines). Today those are 73% of everything under
`docs/` (82% if `decisions/` is excluded, as the brief counts it).

### ⚠ This conflicts with `openspec/specs/documentation/spec.md`

Per `CLAUDE.md` and `CONTRIBUTING.md:206`, a spec/prose discrepancy is
**paused and surfaced**, not silently resolved. Three requirements bind:

1. **`documentation/spec.md:80-94` — "End-user guide is separate from internal
   reference".** The requirement text says the *MkDocs site* SHALL present the
   user narrative "distinct from contributor material (decision log, threat model,
   codec analysis, known issues, open-issues triage) and from the non-normative
   grab-bag", and the scenario reads: *"User opens the docs home | User-guide pages
   are primary navigation; **internal/grab-bag are clearly secondary**"*.

   The spec as written requires internal and grab-bag to be **on the site, ranked
   secondary**. Unpublishing them is a different structure, not a stricter version
   of the same one. It needs a spec delta, not an interpretation.

2. **`documentation/spec.md:65,77` and `packaging-and-extras/spec.md:141`** both
   name **`docs/internal/library-analysis.md` verbatim** as the required home of
   the codec rationale. Any move requires a delta to both.

3. **`documentation/spec.md:86`** — "Gotchas SHALL sit immediately after basic
   usage in primary navigation." The proposed nav honours this; noting it so a
   later reshuffle does not break it by accident.

The spec is authoritative and predates the observation that drove this review (that
a user searching for "PPMd" lands in a 695-line upstream investigation). The
question is whether the spec encoded a decision that still holds, or one that was
made before the internal section grew from a few pages to 3,700 lines.

**Options**

| | Approach | Cost |
|---|---|---|
| **A** *(recommended)* | Unpublish both. `docs/internal/` → `dev-docs/`, `docs/grab-bag/` → `dev-docs/history/`. Amend `documentation` and `packaging-and-extras`. | 1 OpenSpec change; ~35 link repoints incl. 2 runtime error strings; the invariant "everything in `docs/` is published" becomes checkable with no exception list. |
| **B** | Keep the paths, drop them from the site via mkdocs `exclude_docs`. | Cheapest: no path churn, no code-comment or error-string edits, and the two verbatim-path requirements are untouched. **Still needs the delta for requirement 1.** Cost: the nav guardrail now needs a second list kept in sync with the first — the same drift shape that produced six unlisted pages. |
| **C** | Status quo: tidy `docs/internal/` in place, keep it published as a secondary section. | No spec change. Leaves the site 73% non-user material, which is the finding that commissioned this review. |

**Recommendation: A.** The brief's Hard constraints are explicit that URL churn is
free until the `0.2.0` tag and that the reviewer should "propose the structure you
would choose on a blank page". On a blank page nobody publishes a valgrind
transcript and a CI bisect recipe on the user documentation site. B is a
defensible fallback if the spec delta is the objection rather than the structure —
but it buys a config file where A buys an invariant.

**If the answer is C**, say so explicitly: the rest of this review still holds
(the page-shape splits, the ADR-0014 split, the deletions, the guardrails), and
only the tree in [`target-tree.md`](target-tree.md) is affected.

---

## Q2 — Does `docs/decisions/` stay published? ✅ **ANSWERED: yes**

> *"optionally with some curated higher-level implementation details for curious
> users to know what's going on behind the scenes and the major decisions"*

Recorded as [`DECISIONS.md`](DECISIONS.md) D2, which also proposes a new
`docs/how-it-works.md` for the "behind the scenes" half — there is no such page
today. The reasoning below stands.



Strictly, ADRs are "Maintainer + current", so the pure hypothesis unpublishes them
along with everything else in Q1.

**Recommendation: keep them published**, as a named exception. An ADR answers "why
did you write your own 7z parser instead of wrapping `py7zr`?" — that is an
*adoption* question, and Topic 7 (`review/backlog.md:134`) will judge whether the
docs answer it. They are 21–105-line curated records with a maintained index, a
different object from a 695-line investigation. Four user pages already link into
them ten times.

If they are unpublished, **10 working links** from four user pages
(`acknowledgements.md` ×4, `migrating.md` ×3, `support-matrix.md` ×2, `usage.md`
×1) would have to leave the site — worse for the reader, and a Topic 7 regression.

---

## Q3 — What is `gotchas.md` for?

It is required to exist and to sit immediately after basic usage
(`documentation/spec.md:86,175`). But four of its seven sections have a same-titled
section in `costs.md`, and the format table restates `formats.md`
([`page-shape.md`](page-shape.md) §3). The duplication has already drifted
([`observations.md`](observations.md) O-2).

| | Approach |
|---|---|
| **A** *(recommended)* | Keep the page and its slot; each bullet becomes one line + a link to the owning page. Target ~80 lines from 156. |
| **B** | Keep it as a full restatement and accept the drift risk, mitigated by a periodic sync pass. |
| **C** | Dissolve it into the owning pages. **Requires a `documentation` spec delta** — the page is required by name. |

**Recommendation: A.** It preserves the page's actual value (a curated "read this
next" digest) and removes the reason a fact would ever be written down twice. Worth
confirming that a one-line-plus-link entry still satisfies
`documentation/spec.md:175`, which is about coverage and framing rather than
length — my reading is that it does, but it is your spec.

---

## Q4 — Split ADR 0014?

`docs/decisions/0014-integrity-verdicts-from-reads-not-close.md` is **615 lines** —
59% of the ADR corpus, ~25× the median ADR (24 lines). It contains an
`## Open questions` section while marked `Status: accepted`
([`observations.md`](observations.md) O-6), a 56-line `## Guarantee (for users)`
section, and 100+ lines of trade-off analysis. It is also the ADR that fell out of
the nav.

**Recommendation: split three ways** — a ~30-line ADR matching the shape of the
other 13 stays in `docs/decisions/`; the investigation and trade-off analysis go to
`dev-docs/investigations/`; the user guarantee moves into the new `docs/reading.md`
(it is user documentation that currently only exists inside an unlisted ADR).

**Counter-argument to weigh:** the depth is *why* the decision is credible, and a
reader who follows a link from `compressed-streams` wants the full reasoning in one
place. If you prefer it whole, the minimum fix is adding it to the nav and
resolving or relocating the `## Open questions` section (it overlaps the open
`verification-integrity-mode` proposal, PR #185).

---

## Q5 — `AGENTS.md` or `CLAUDE.md`: which is canonical?

`AGENTS.md` (83 lines) opens by deferring to `CLAUDE.md` (117 lines). Overlap is
roughly 20 lines (formatting-before-commit, the three-config rule, `uv`). The rest
is complementary: `AGENTS.md` has ~60 lines of Cursor Cloud environment detail;
`CLAUDE.md` has the repo map, the `openspec` CLI setup, the `archivey-dev`
reference-repo instructions, and the 7z/RAR strategy.

`AGENTS.md` also contains **two statements that are false today**
([`observations.md`](observations.md) O-1: the CLI and the native 7z/RAR readers
are described as unimplemented). The file that is not canonical is the one that
rotted — which is the argument for having one.

**Recommendation: one canonical file, `AGENTS.md`**, absorbing `CLAUDE.md`'s
content, with `CLAUDE.md` reduced to a pointer (Claude Code auto-loads it, so it
cannot simply be deleted). `AGENTS.md` is the tool-neutral convention and does not
privilege one agent vendor.

**Watch out on merge:** the two files give *different* `openspec` install commands
on purpose — `CLAUDE.md:31` uses `npm install -g`, `AGENTS.md:68-71` notes that
fails with `EACCES` on Cursor Cloud and uses `--prefix "$HOME/.local"`. Both are
correct for their environment; a careless merge will drop one.

Choosing `CLAUDE.md` as canonical instead is equally workable — the decision is
which convention you want to maintain, and either way the stale statements get
fixed.

---

## Q6 — Do `PLAN.md` and `IDEAS.md` leave the repo root?

The brief notes the root "mixes every audience". After deleting the four stubs and
merging the agent guides, the root is: `README` (user), `CHANGELOG`/`SECURITY`
(user + release), `CONTRIBUTING`/`AGENTS` (contributor), `VISION`/`PLAN`/`IDEAS`
(product direction). Eight files, down from thirteen.

**Recommendation: leave all eight.** Six of the eight are at root because a tool or
convention expects them there. `VISION.md` is linked from `README.md` and is the
declared tie-breaker for every review brief. `PLAN.md` and `IDEAS.md` are the only
two with a real case for moving — and they carry ~20 inbound references between
them (`openspec/project.md`, `review/STATUS.md`, six archived reviews, four ADRs,
`threat-model.md`). Moving them is churn for symmetry, which the brief warns
against.

If you want them moved anyway, `dev-docs/` is the right destination and it is a
mechanical addition to the phase-3 commit list.

---

## Q7 — The dual-audience pages: `threat-model.md` and `known-issues.md`

The brief flags these as the deliberate tension to resolve rather than paper over.
They resolve **differently**, which is the useful finding.

### `threat-model.md` (320 lines) — split

Two documents in one file:

- **Lines 9–58** (`## Trust boundaries` + `## What is already enforced`) — the
  honest security-posture statement an evaluating user wants. Today the *only*
  place the three-layer symlink defence, the extraction-root overwrite rejection,
  and the atomic-write semantics are written down. `docs/safe-extraction.md:28`
  already sends users here.
- **Lines 60–320** (`## OPEN gaps — security` O1–O8, `## OPEN gaps —
  compatibility` C1–C4) — a maintainer triage register keyed to OpenSpec changes,
  with measured slip-through rates and py7zr internals.

**Recommendation:** the enforced-guarantees half moves into `docs/safe-extraction.md`
(where it is a large part of the ~3× growth the independent pass argues for); the
gap register stays as `dev-docs/threat-model.md`. `VISION.md:28` and `SECURITY.md:73`
repoint at the new register path.

**The judgement call for you:** an unpublished gap register means an evaluating
user cannot read your open security gaps. That can be read as prudence or as
opacity. My reading is that O1–O8 are *design-note* granularity, not embargoed
vulnerabilities, and `SECURITY.md` already carries the disclosure posture — but
publishing your own open-gap list is a positioning decision, not a filing one.

### `known-issues.md` (709 lines) — **no split**

Read end to end it is valgrind output, GitHub Actions run IDs, CI workflow
bandages, version-matrix soak tables, and bisect recipes. The four things a user
needs from it are **already** summarised in `gotchas.md` and `costs.md` with a link
back: do not close a source under a live accelerator stream; leave accelerators off
for untrusted input under a latency budget; `import archivey` patches pycdlib
process-globally; bare-`.gz` truncation detection is best-effort.

**Recommendation:** move the whole file to `dev-docs/known-issues.md` and convert
the two user-page links (`gotchas.md:144`, `costs.md:143`) to absolute GitHub URLs
per D3.
No user loses anything. Also fix its index description
([`observations.md`](observations.md) O-8) and the two runtime error strings that
cite its path (O-12).

---

## Q8 — Deletions

Only one set of files is proposed for deletion:

| File | Lines | Why |
|---|---:|---|
| `ARCHITECTURE.md` | 7 | "Moved to…" stub from an earlier move |
| `ASYNC.md` | 5 | Same |
| `COMPARISON.md` | 5 | Same |
| `SPEC.md` | 6 | Same |

With URL churn free until the tag, a tombstone is clutter rather than a pattern to
copy. **Unverified:** whether any external page links these four. Nothing is on
real PyPI and only `0.2.0.dev0` reached TestPyPI, so no release artifact points at
them; a stray external link cannot be ruled out from inside the repo. If you want
zero risk, keeping four 6-line files costs nothing.

**Nothing else is proposed for deletion**, including the 3,068 lines of
`grab-bag/`. Those are cited by five ADRs *by section number* for provenance
(`0001` §5.6, `0002` §5.7, `0005` §5.3, `0006` §5.1, `0007` §2.1/§5.2), by
`IDEAS.md:6` ("`SPEC.md` Appendix A"), and `release-repo-cutover.md:64` explicitly
says to leave `COMPARISON.md` unchanged as a historical record. Git history is not
a substitute for a document another document cites by section.

**Counter-question for you:** is that provenance chain worth 3,068 lines, or would
you rather cut the citations and delete the prose? That is a decision about how
much archaeology the project keeps, and it is yours.

---

## Q9 — Directory name for the unpublished maintainer tree

If Q1 = A, the material needs a home. `dev-docs/` is used throughout these
documents as a placeholder.

Alternatives: `maintainers/`, `internal-docs/`, `notes/`, or `contributing/`
(reads oddly next to root `CONTRIBUTING.md`). Purely a naming preference — say the
word and phase 3 uses it.

---

## Summary of what a decision unblocks

| Q | Blocks |
|---|---|
| **Q1** | The whole tree; the spec delta; Q2, Q3, Q7, Q9 |
| Q2 | Whether `decisions/` appears in the nav |
| Q3 | Whether `gotchas.md` shrinks (needs a delta only under option C) |
| Q4 | Whether ADR 0014 splits, and where its user guarantee lands |
| Q5 | One commit, independent of everything else |
| Q6 | Two `git mv`s, independent of everything else |
| Q7 | The size of `safe-extraction.md` and the shape of the gap register |
| Q8 | Four deletions |
| Q9 | Naming only |

Q5, Q6 and Q8 can be answered and executed independently of Q1 if you want
something to land while Q1 is still open.
