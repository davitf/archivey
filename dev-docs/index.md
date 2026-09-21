# Developer docs

Maintainer / contributor material. Deliberately **not published** to the docs
site: everything under `docs/` is for users, and everything here is not.

| Doc | Role |
| --- | --- |
| [Pair workflow](pair-workflow.md) | **Preferred everyday loop**: investigate → grill into handbook → thin brief → implement → other-agent review → decision packets |
| [Review loop](review-loop.md) | How steps 4–6 of the pair workflow run without a driver: what triggers each hop, the `loop:*` labels that hold the state, and the three-round cap |
| [Code map](code-map.md) | Where to start for a given change: tree shape, the path through a read, task→files, and which doc answers which kind of question |
| Format / topic handbook | [`formats/zip.md`](formats/zip.md) — the first page, and the worked example for the shape (pair-workflow §Format page structure) · [`formats/rar.md`](formats/rar.md) — the only format whose read path crosses a process boundary · [`formats/7z.md`](formats/7z.md) — the format whose header is a decode program · [`topics/prefixed-archives.md`](topics/prefixed-archives.md) — archives that do not start at byte 0 · [`topics/stream-ownership.md`](topics/stream-ownership.md) — which wrapper closes its inner. Create `formats/<format>.md` or `topics/<topic>.md` with the first change that needs it; do not add empty directories |
| [Threat model](threat-model.md) | Trust boundaries, enforced guarantees, open security/compat gaps |
| [Open issues (gotchas triage)](open-issues.md) | Fixable leftovers vs irreducible user gotchas; docs/spec drift |
| [Open work inventory](open-work-inventory.md) | Dated cross-register snapshot: which open PRs, OpenSpec changes and register entries are live, what blocks what, and what is already dead. Indexes the registers; never the source of truth for one |
| [Compression-library analysis](library-analysis.md) | Per-codec backend choice and rationale |
| [Known issues](known-issues.md) | Defect/contract forensics: upstream bugs, our mitigations, and the evidence behind them |
| [Release checklist](release-checklist.md) | Every-release loop: CHANGELOG, perf vs prior tag, docs, tag/publish |
| [Release-repo cutover](release-repo-cutover.md) | One-time rename / PyPI / Pages before the first public tag |
| [Decision log](decisions/index.md) | Rare repo-wide ADRs; prefer light notes on format/topic handbook pages for new decisions |
| [Investigations](investigations/) | Finished evidence: PPMd, pyppmd/rapidgzip/pybcj/py7zr upstream reports, parallel-reader, [`alternative RAR decompressors`](investigations/alternative-rar-decompressors.md), [`capability declaration vs behaviour`](investigations/capability-declaration-vs-behaviour.md) |
| [Discussions](discussions/) | Design questions written for circulation. Includes [pair-workflow adoption](discussions/2026-09-pair-workflow-adoption.md) and [specs → handbook + tests](discussions/2026-09-specs-to-handbook-and-tests.md) (thin-as-you-go) |
| [History](history/index.md) | Superseded prose (SPEC / ARCHITECTURE / COMPARISON / ASYNC) |
| [PLAN.md](PLAN.md) · [IDEAS.md](IDEAS.md) | Phase roadmap; speculative backlog |

**Maintainer reading surface:** pair workflow + handbook pages above.
`openspec/specs/` remain the **authoritative contract** for agents/CI, not the primary
human UI. Product framing: `VISION.md` at the repository root.
