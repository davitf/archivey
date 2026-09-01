# Developer docs

Maintainer / contributor material. Deliberately **not published** to the docs
site: everything under `docs/` is for users, and everything here is not.

| Doc | Role |
| --- | --- |
| [Code map](code-map.md) | Where to start for a given change: tree shape, the path through a read, task→files, and which doc answers which kind of question |
| [Threat model](threat-model.md) | Trust boundaries, enforced guarantees, open security/compat gaps |
| [Open issues (gotchas triage)](open-issues.md) | Fixable leftovers vs irreducible user gotchas; docs/spec drift |
| [Compression-library analysis](library-analysis.md) | Per-codec backend choice and rationale |
| [Known issues](known-issues.md) | Defect/contract forensics: upstream bugs, our mitigations, and the evidence behind them |
| [Release checklist](release-checklist.md) | Every-release loop: CHANGELOG, perf vs prior tag, docs, tag/publish |
| [Release-repo cutover](release-repo-cutover.md) | One-time rename / PyPI / Pages before the first public tag |
| [Decision log](decisions/index.md) | ADR-style records of load-bearing choices |
| [Investigations](investigations/) | Finished evidence: PPMd, pyppmd/rapidgzip upstream reports, parallel-reader, [`unar` as RAR decompressor](investigations/unar-as-rar-decompressor.md) |
| [Discussions](discussions/) | Design questions written for circulation — context and options, no decision *at the time of writing*. Each entry carries a RESOLVED header once settled, pointing at the change that settled it; the body is left as circulated |
| [History](history/index.md) | Superseded prose (SPEC / ARCHITECTURE / COMPARISON / ASYNC) |
| [PLAN.md](PLAN.md) · [IDEAS.md](IDEAS.md) | Phase roadmap; speculative backlog |

Normative behavior remains in `openspec/specs/`. Product framing: `VISION.md` at the
repository root. Decision summaries: [decision log](decisions/index.md).
