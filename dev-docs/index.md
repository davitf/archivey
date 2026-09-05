# Developer docs

Maintainer / contributor material. Deliberately **not published** to the docs
site: everything under `docs/` is for users, and everything here is not.

| Doc | Role |
| --- | --- |
| [Pair workflow](pair-workflow.md) | **Preferred everyday loop**: investigate → grill into handbook → thin brief → implement → other-agent review → decision packets |
| [Code map](code-map.md) | Where to start for a given change: tree shape, the path through a read, task→files, and which doc answers which kind of question |
| Format / topic handbook | [`formats/zip.md`](formats/zip.md) — the first page, and the worked example for the shape (pair-workflow §Format page structure) · [`formats/rar.md`](formats/rar.md) — the only format whose read path crosses a process boundary · [`topics/prefixed-archives.md`](topics/prefixed-archives.md) — archives that do not start at byte 0. Create `formats/<format>.md` or `topics/<topic>.md` with the first change that needs it; do not add empty directories |
| [Threat model](threat-model.md) | Trust boundaries, enforced guarantees, open security/compat gaps |
| [Open issues (gotchas triage)](open-issues.md) | Fixable leftovers vs irreducible user gotchas; docs/spec drift |
| [Compression-library analysis](library-analysis.md) | Per-codec backend choice and rationale |
| [Known issues](known-issues.md) | Defect/contract forensics: upstream bugs, our mitigations, and the evidence behind them |
| [Release checklist](release-checklist.md) | Every-release loop: CHANGELOG, perf vs prior tag, docs, tag/publish |
| [Release-repo cutover](release-repo-cutover.md) | One-time rename / PyPI / Pages before the first public tag |
| [Decision log](decisions/index.md) | Rare repo-wide ADRs; prefer light notes on format/topic handbook pages for new decisions |
| [Investigations](investigations/) | Finished evidence: PPMd, pyppmd/rapidgzip upstream reports, parallel-reader, [`alternative RAR decompressors`](investigations/alternative-rar-decompressors.md) |
| [Discussions](discussions/) | Design questions written for circulation. Includes [pair-workflow adoption](discussions/2026-09-pair-workflow-adoption.md) and [specs → handbook + tests](discussions/2026-09-specs-to-handbook-and-tests.md) (thin-as-you-go) |
| [History](history/index.md) | Superseded prose (SPEC / ARCHITECTURE / COMPARISON / ASYNC) |
| [PLAN.md](PLAN.md) · [IDEAS.md](IDEAS.md) | Phase roadmap; speculative backlog |

**Maintainer reading surface:** pair workflow + handbook pages above.
`openspec/specs/` remain the **authoritative contract** for agents/CI, not the primary
human UI. Product framing: `VISION.md` at the repository root.
