# History

Superseded prose, kept for provenance: several ADRs cite these documents, and
`release-repo-cutover.md` treats them as the historical record. Material that does
**not** belong in the end-user guide or the curated decision log, but must not be
deleted. None of it is normative.

| Doc | Likely status | Notes |
| --- | --- | --- |
| [SPEC.md](SPEC.md) | **Superseded as authority** by `openspec/specs/` | Large prose contract; useful archaeology; may drift |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Partially superseded | Module layout + trade-offs; load-bearing “why” extracted to `dev-docs/decisions/` |
| [COMPARISON.md](COMPARISON.md) | Historical | DEV vs clean-slate comparison; Intent-enum recommendation later reversed |
| [PLAN.md](PLAN.md) | Historical | Pre-0.2.0 phase roadmap. Current state: open OpenSpec changes, `IDEAS.md`, `CHANGELOG.md`; writing design in `investigations/archive-writing-design.md` |
| [2026-09-pair-workflow-adoption.md](2026-09-pair-workflow-adoption.md) | Resolved | Adopting the pair workflow (#280); the live loop is `pair-workflow.md` |
| [ASYNC.md](ASYNC.md) | Exploration | Not a v1 decision; sync-only stands; seams still interesting |
| [parallel-reader.md](../investigations/parallel-reader.md) | Exploration → mostly landed | Filed under `investigations/` — still cited from `src/`. Concurrent-member-streams superseded much of this; keep for audit notes / benchmarks pointers |

No redirect stubs remain at the old root paths for `SPEC.md` / `ARCHITECTURE.md` /
`COMPARISON.md` / `ASYNC.md`; link the copies here.
