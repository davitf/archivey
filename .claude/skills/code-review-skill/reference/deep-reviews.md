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
6. **Don’t re-litigate settled ground** — check archive tables + `STATUS.md` for
   already-closed findings before spending budget.
7. **Archive lifecycle** — only move a review to `review/archive/` when every
   actionable item is fixed or consciously deferred (`STATUS.md` / `backlog.md`).

Review themes to know. **`review/STATUS.md` is the live index — read it rather than this
table**, which records lenses, not state. A theme listed as archived means findings in
that area are *re-reviews*: check the archive tables first so you do not re-litigate
settled ground (`review/backlog.md` carries the deferred topics and their reasons).

| Review | Lens | State |
|--------|------|-------|
| `docs/` | Documentation IA, then content accuracy/gaps (Topic 8) | **In flight** — see `STATUS.md` |
| `api-coherence/` | Uniform interface, surface size, CLI-as-consumer gaps | Archived |
| `performance/` | ≤1.3× budget, gate efficacy, solid/listing hotspots | Archived |
| `debt-ledger/` | Freeze-cost debt; corpus matrix (`corpus-matrix.md`) | Archived |
| `stream-layering/` | Wrapper correctness + collapse | Archived |
| `cli-product/` | CLI UX / grammar / exit codes (product, not correctness) | Archived |
| `simplicity-consistency/` | Topic 9 — duplicated concepts, inconsistent surfaces | Archived 2026-08-15 |
| Security round | Hostile input, crypto, RAR, stream decoder | Archived |
