# Developer docs

Maintainer / contributor material. Deliberately **not published** to the docs
site: everything under `docs/` is for users, and everything here is not.

| Doc | Role |
| --- | --- |
| [Pair workflow](pair-workflow.md) | **Preferred everyday loop**: investigate → grill into handbook → thin brief → implement → other-agent review → decision packets |
| [Code map](code-map.md) | Where to start for a given change: tree shape, the path through a read, task→files, and which doc answers which kind of question |
| [Format handbook](formats/README.md) | Living per-format pages (create on first real use): behaviour, consequences, light decisions, verify |
| [Topic handbook](topics/README.md) | Living cross-cutting pages; link registers (e.g. threat model), do not restate them |
| [Threat model](threat-model.md) | Trust boundaries, enforced guarantees, open security/compat gaps |
| [Open issues (gotchas triage)](open-issues.md) | Fixable leftovers vs irreducible user gotchas; docs/spec drift |
| [Compression-library analysis](library-analysis.md) | Per-codec backend choice and rationale |
| [Known issues](known-issues.md) | Defect/contract forensics: upstream bugs, our mitigations, and the evidence behind them |
| [Release checklist](release-checklist.md) | Every-release loop: CHANGELOG, perf vs prior tag, docs, tag/publish |
| [Release-repo cutover](release-repo-cutover.md) | One-time rename / PyPI / Pages before the first public tag |
| [Decision log](decisions/index.md) | Rare repo-wide ADRs; prefer light notes on format/topic handbook pages for new decisions |
| [Investigations](investigations/) | Finished evidence: PPMd, pyppmd/rapidgzip upstream reports, parallel-reader, [`alternative RAR decompressors`](investigations/alternative-rar-decompressors.md) |
| [Discussions](discussions/) | Design questions written for circulation — context and options, no decision *at the time of writing*. Each entry carries a RESOLVED header once settled. Includes [pair-workflow adoption](discussions/2026-09-pair-workflow-adoption.md) |
| [History](history/index.md) | Superseded prose (SPEC / ARCHITECTURE / COMPARISON / ASYNC) |
| [PLAN.md](PLAN.md) · [IDEAS.md](IDEAS.md) | Phase roadmap; speculative backlog |

**Maintainer reading surface:** pair workflow + handbook pages above.
`openspec/specs/` remain the **authoritative contract** for agents/CI, not the primary
human UI. Product framing: `VISION.md` at the repository root.
