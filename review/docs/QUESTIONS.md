# Questions for the maintainer

Phase 2 of the docs IA review is "Decide" (`brief.md`). These are the calls that
are product/ownership decisions, not reviewer decisions. Each has a
recommendation; none is decided here.

> **Q1–Q7 are answered** — see [`DECISIONS.md`](DECISIONS.md).
> … **D8:** threat-model three-way. **D9:** `known-issues.md` → `dev-docs/` +
> **required follow-up triage** (resolved / mitigated / upstream / fixable / evidence).
> Q8–Q9 still open.

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

## Q2 — Does `docs/decisions/` stay published? ✅ **ANSWERED: no (revised 2026-08-02)**

> *2026-07-29: "optionally with some curated higher-level implementation details
> for curious users… and the major decisions" — initially read as keeping the ADR
> log published.*
>
> *2026-08-02: a summary of technical decisions (with links to ADRs / OpenSpec
> changes) is fine; the whole raw ADR corpus is not. Summary lives on the single
> `how-it-works.md` page; raw ADRs move to `dev-docs/decisions/`; user-page ADR
> links are dropped unless the ADR has end-user depth that cannot be inlined.*

Recorded as [`DECISIONS.md`](DECISIONS.md) D2 (revised). The original
recommendation below is superseded — kept for provenance of why the first answer
went the other way.

Strictly, ADRs are "Maintainer + current", so the pure hypothesis unpublishes them
along with everything else in Q1.

**Original recommendation (superseded): keep them published**, as a named
exception. An ADR answers "why did you write your own 7z parser instead of
wrapping `py7zr`?" — that is an *adoption* question. They are 21–105-line curated
records with a maintained index, a different object from a 695-line investigation.
Four user pages already link into them ten times.

**What we actually decided:** the adoption answer belongs in a short published
summary on `how-it-works.md`; the ADR files themselves are provenance for
maintainers and move with the rest of the unpublished tree. The ten user-page
links are inlined-then-dropped under D2's rule (GitHub only if uninlinable).

---

## Q3 — What is `gotchas.md` for? ✅ **ANSWERED: A + footgun rule (D4)**

> *"agree with A. … criteria for being there is: what must the user know to avoid
> making mistakes or shooting themselves in the foot?"*

Recorded as [`DECISIONS.md`](DECISIONS.md) D4. Shape = option A (keep slot; one
line + link). Two sections: **should/shouldn't do** (cost/API footguns) and
**be aware of** (honesty / verification gaps). Spec conflict at
`documentation/spec.md:175` surfaced for the D1 delta; quartet triage complete
under D4.

It is required to exist and to sit immediately after basic usage
(`documentation/spec.md:86,175`). But four of its seven sections have a same-titled
section in `costs.md`, and the format table restates `formats.md`
([`page-shape.md`](page-shape.md) §3). The duplication has already drifted
([`observations.md`](observations.md) O-2).

| | Approach |
|---|---|
| **A** *(chosen)* | Keep the page and its slot; each bullet becomes one line + a link to the owning page. Target shrinks further under the footgun rule. |
| **B** | Keep it as a full restatement and accept the drift risk, mitigated by a periodic sync pass. |
| **C** | Dissolve it into the owning pages. **Requires a `documentation` spec delta** — the page is required by name. |

**Recommendation was A** — confirmed. The sharper inclusion rule supersedes the
earlier assumption that the spec-mandated format-limitation quartet must live here.

---

## Q4 — Split ADR 0014? ✅ **ANSWERED: three ways (D5)**

> *"three ways"*

Recorded as [`DECISIONS.md`](DECISIONS.md) D5. Slim ADR → `dev-docs/decisions/`;
investigation → `dev-docs/investigations/`; user guarantee → `docs/reading.md`.

`docs/decisions/0014-integrity-verdicts-from-reads-not-close.md` is **615 lines** —
59% of the ADR corpus, ~25× the median ADR (24 lines). It contains an
`## Open questions` section while marked `Status: accepted`
([`observations.md`](observations.md) O-6), a 56-line `## Guarantee (for users)`
section, and 100+ lines of trade-off analysis. It is also the ADR that fell out of
the nav.

**Recommendation was the three-way split** — confirmed.

---

## Q5 — `AGENTS.md` or `CLAUDE.md`: which is canonical? ✅ **ANSWERED: AGENTS.md (D6)**

> *"AGENTS.md canonical, Claude just pointer. if there's Claude-specific
> environment info, then that can remain on Claude.md"*

Recorded as [`DECISIONS.md`](DECISIONS.md) D6.

`AGENTS.md` (83 lines) opens by deferring to `CLAUDE.md` (117 lines). Overlap is
roughly 20 lines (formatting-before-commit, the three-config rule, `uv`). The rest
is complementary: `AGENTS.md` has ~60 lines of Cursor Cloud environment detail;
`CLAUDE.md` has the repo map, the `openspec` CLI setup, the `archivey-dev`
reference-repo instructions, and the 7z/RAR strategy.

`AGENTS.md` also contains **two statements that are false today**
([`observations.md`](observations.md) O-1: the CLI and the native 7z/RAR readers
are described as unimplemented). The file that is not canonical is the one that
rotted — which is the argument for having one.

**Recommendation was `AGENTS.md` canonical** — confirmed. `CLAUDE.md` becomes a
pointer, retaining only Claude-specific environment notes.

---

## Q6 — Do `PLAN.md` and `IDEAS.md` leave the repo root? ✅ **ANSWERED: move (D7)**

> *"move them to keep the root cleaner. we're going to rewrite/cleanup most docs
> anyway and those references might even be removed or should be reorganized"*

Recorded as [`DECISIONS.md`](DECISIONS.md) D7. Destination: `dev-docs/PLAN.md`
and `dev-docs/IDEAS.md`. Inbound refs are cleaned up in the broader docs rewrite,
not treated as a reason to keep them at root.

The brief notes the root "mixes every audience". After deleting the four stubs and
merging the agent guides, the root is: `README` (user), `CHANGELOG`/`SECURITY`
(user + release), `CONTRIBUTING`/`AGENTS` (contributor), `VISION`/`PLAN`/`IDEAS`
(product direction). Eight files, down from thirteen.

**Original recommendation was leave all eight** — superseded; maintainer chose
root cleanliness over citation-churn avoidance.

---

## Q7 — The dual-audience pages: `threat-model.md` and `known-issues.md`

### `threat-model.md` (320 lines) — ✅ **ANSWERED: three-way (D8)**

> Enforced → `safe-extraction`; user-mitigable residuals → Gotchas (incl. O6
> nesting wording); maintainer backlog → `dev-docs/threat-model.md`. C3 metadata
> fidelity stays an idea (+ optional “not yet supported” in user docs, not a
> gotcha). Unpublished ≠ safer — filing is by audience.

Recorded as [`DECISIONS.md`](DECISIONS.md) D8. Reasoning below kept for provenance.

Two documents in one file:

- **Lines 9–58** (`## Trust boundaries` + `## What is already enforced`) — the
  honest security-posture statement an evaluating user wants. Today the *only*
  place the three-layer symlink defence, the extraction-root overwrite rejection,
  and the atomic-write semantics are written down. `docs/safe-extraction.md:28`
  already sends users here.
- **Lines 60–320** (`## OPEN gaps — security` O1–O8, `## OPEN gaps —
  compatibility` C1–C4) — a maintainer triage register keyed to OpenSpec changes,
  with measured slip-through rates and py7zr internals.

**Original recommendation** was enforced → safe-extraction, gaps unpublished.
**Refined:** strip closed items; move user-mitigable residuals to Gotchas; keep
only backlog in `dev-docs/`.

### `known-issues.md` (709 lines) — ✅ **ANSWERED: move whole + triage follow-up (D9)**

> Move to `dev-docs/known-issues.md`; no published subset. **Required follow-up:**
> classify sections (resolved / mitigated / upstream unfixable / we-can-fix /
> evidence-only) so the file does not stay an unwieldy dump. Sibling roles vs
> IDEAS / open-issues / threat-model / investigations recorded under D9. Gotchas
> accelerator bullet rewritten for `_TrappingSource` mitigation.

Recorded as [`DECISIONS.md`](DECISIONS.md) D9.

Read end to end it is valgrind output, GitHub Actions run IDs, CI workflow
bandages, version-matrix soak tables, and bisect recipes. User-needed facts live
(or will live) on Gotchas / formats / SECURITY under D4/D8.

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
| Q2 | Whether raw ADRs appear in the nav (no — summary on `how-it-works.md`) |
| Q3 | Gotchas = two sections (should/shouldn't + be aware of); triage ✅ D4 |
| Q4 | ADR 0014 three-way split (✅ D5) |
| Q5 | `AGENTS.md` canonical; `CLAUDE.md` pointer (✅ D6) |
| Q6 | `PLAN`/`IDEAS` → `dev-docs/` (✅ D7) |
| Q7 | A ✅ D8; B ✅ D9 (`known-issues` move + triage follow-up) |
| Q8 | Four deletions |
| Q9 | Naming only |

Q5, Q6 and Q8 can be answered and executed independently of Q1 if you want
something to land while Q1 is still open.
