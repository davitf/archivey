# Deep reviews (`review/`) — when the skill expands into a brief

> Loaded only for a **commissioned deep review**, not for ordinary PR review
> ([`SKILL.md`](../SKILL.md) §1 routes here). Reporting, severity and posting stay in
> `SKILL.md`; the code checklists are in [`code-pr.md`](code-pr.md).

A commissioned deep review inherits
[`review/README.md`](../../../../review/README.md):

1. **Baseline first** — record green gates (pytest / skips, pyrefly, ty, ruff) and which
   dependency config. Overrides the no-re-run default (`SKILL.md` §6) — no CI run to
   inherit.
2. **VISION ranking** — order findings by load-bearing claims (`code-pr.md` §What you are
   reviewing).
3. **Deliverable shape** — `SUMMARY.md` (headline + severity table + status), theme
   files, `QUESTIONS.md` for maintainer decisions, and a **“what is actually fine”**
   section.
4. **Evidence** — `file:line`, concrete triggering input/state, runnable repro when
   practical.
5. **Pause and ask** — spec/design conflicts go to `QUESTIONS.md`, not silent fixes
   (including “the spec is wrong; here’s the better contract”).
6. **Don’t re-litigate settled ground** — check the archived reviews' `SUMMARY.md`
   files for already-closed findings before spending budget.
7. **Archive lifecycle** — only move a review to `review/archive/` when every
   actionable item is fixed or consciously deferred (deferred work is tracked internally).

**Which reviews are open:** the top level of `review/`
([`review/README.md`](../../../../review/README.md)). A theme already archived means
findings in that area are *re-reviews*: read its `review/archive/<date>-<name>/SUMMARY.md`
first.
