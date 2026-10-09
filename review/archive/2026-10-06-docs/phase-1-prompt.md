# Commissioning prompt — docs IA review, phase 1 (audit)

The exact prompt handed to the fresh agent that runs phase 1 of
[`brief.md`](brief.md), recorded so the run is reproducible and so a later pass can see
what the agent was and was not told. Written 2026-07-29 against `main` @ `ce674bf`.

Deliberately thin: it points at the brief rather than restating it. Duplicating the
inventory and findings here would create a second source that drifts from the first.

---

```
Run phase 1 of the documentation information-architecture review for the archivey repo.

## Start here

Read `review/docs/brief.md` in full — it is the authoritative brief for this task and
was written specifically for you. It contains the ask, a measured inventory, the known
problems (do not re-derive them), the organising hypothesis, scope, out-of-scope, hard
constraints, and the four-phase process. Follow it.

Then read, as inputs rather than instructions:
- `review/docs/independent/` — an independent code-derived outline produced by an agent
  that was denied all existing docs. Its headline finding (safe extraction is 6.3% of our
  guide vs ~25% proposed) is a phase-1 finding you must act on. Note the caveats recorded
  in the brief: it over-reports because it cannot see intent, and its *agreements* with
  our structure are weak evidence.
- `review/docs/code-self-documentation.md` and `api-surface-suggestions.md` — already
  triaged; do not redo that work.
- `review/README.md` — the review lifecycle and the conventions every brief inherits.
- `CLAUDE.md` and `CONTRIBUTING.md` — repo orientation and standards.

## Verify one constraint before you start

The brief's Hard constraints section should say that **URL churn is free until the
`0.2.0` tag**. If instead you find "Published URLs must not 404" listed as a hard
constraint, you are reading a stale checkout — re-pull. That earlier version biased the
review: it pushes toward keeping pages published where they are, purely to avoid 404s on
a site with no release behind it. Nothing has been released (only `0.2.0.dev0` on
TestPyPI, a scratch index). Propose the structure you would choose on a blank page. Do
not design around 404s and do not propose a redirects dependency.

Internal consistency still applies: any page move implies updating the README's absolute
docs-site URLs, `mkdocs.yml` nav, `IDEAS.md`, and code comments that reference doc paths.

## This is phase 1 only: analysis, no migration

**Do not move, rename, or delete any file.** Do not edit any page's content. The output
is documents under `review/docs/` that a maintainer can review and decide on:

- `SUMMARY.md` — headline, the proposed target tree, a top-findings table, and a "what is
  actually fine" section (the review/ and openspec/ lifecycles, the ADR log, and the core
  user pages are working — do not churn them for symmetry).
- `inventory.md` — the full file-by-file table: every prose file in the repo (root,
  `docs/**`, `review/**`, `openspec/**`, `.claude/**`) with audience, current home,
  proposed home, and a one-line rationale. "Delete" and "keep where it is" are valid
  assignments; silence is not. This table is the deliverable the migration will be
  executed from, so completeness matters more than prose quality.
- `QUESTIONS.md` — the decisions only the maintainer can make: the taxonomy itself, what
  the published site includes, deletions, and specifically the dual-audience cases
  (`threat-model.md`, `known-issues.md`). Give a recommendation per question, but do not
  decide them.
- `observations.md` — content problems you notice while reading (inaccuracies vs the code,
  duplication, pages that are really three pages). Record only; acting on them is Topic 8.
- Theme files as useful.

## Rules

- Cite paths and line counts. Where a claim depends on something you did not verify, say
  so rather than assuming.
- Where prose and `openspec/specs/` disagree, **pause and ask** — do not edit either to
  match. The specs are authoritative.
- Two content decisions ARE in scope because deferring them would misfile pages:
  merge/split/delete, and dual-audience splits. Everything else about content waits.
- The `openspec` CLI is not preinstalled: `npm install -g @fission-ai/openspec`.
- No source code changes, so the three-config test matrix is not needed. `mkdocs build
  --strict` must stay green if you touch anything the docs build sees.

## Branch and PR

Work on the existing branch `claude/review-docs-gaps-priorities-9nzy2s`, which already
has an open draft PR containing this prompt. Commit your artifacts there and push to that
branch — **do not open a new PR.** The prompt and the audit it produced should land as a
single reviewable unit.

## Done when

A maintainer can read `SUMMARY.md` + `QUESTIONS.md`, answer the questions, and hand
`inventory.md` to an implementer as a migration worklist — without needing to re-read the
source docs to check your reasoning.
```
