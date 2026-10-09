# Developer docs

Maintainer / contributor material. Deliberately **not published** to the docs
site: everything under `docs/` is for users, and everything here is not.

| Doc | Role |
| --- | --- |
| [Pair workflow](pair-workflow.md) | **Preferred everyday loop**: investigate → grill into handbook → thin brief → implement → other-agent review → decision packets |
| [Review loop](review-loop.md) | How steps 4–6 of the pair workflow run: the `review` label that starts a round, how rounds are counted, and the five-round cap |
| [Code map](code-map.md) | Where to start for a given change: tree shape, the path through a read, task→files, and which doc answers which kind of question |
| Format / topic handbook | [`formats/zip.md`](formats/zip.md) — the first page, and the worked example for the shape (pair-workflow §Format page structure) · [`formats/rar.md`](formats/rar.md) — the only format whose read path crosses a process boundary · [`formats/7z.md`](formats/7z.md) — the format whose header is a decode program · [`formats/tar.md`](formats/tar.md) — no index, stdlib `tarfile`, and how archivey decides why a walk ended · [`formats/iso.md`](formats/iso.md) — a filesystem read through a library written to author it · [`formats/dmg.md`](formats/dmg.md) — a UDIF image, recognised by the `koly` block and refused · [`formats/single-file.md`](formats/single-file.md) — the stream codecs as one-member archives, with a page per codec: [gzip](formats/gzip.md), [bzip2](formats/bzip2.md), [xz, lzip and LZMA Alone](formats/xz.md), [zstd and LZ4](formats/zstd-lz4.md), [Brotli](formats/brotli.md), [`.Z`](formats/unix-compress.md) · [`formats/directory.md`](formats/directory.md) — a live tree behind the archive API, read at walk time and again at open · [`topics/detection.md`](topics/detection.md) — how a source's format is decided, in what order, and at what cost · [`topics/prefixed-archives.md`](topics/prefixed-archives.md) — archives that do not start at byte 0 · [`topics/stream-ownership.md`](topics/stream-ownership.md) — which wrapper closes its inner · [`topics/exception-handlers.md`](topics/exception-handlers.md) — when a blind `except Exception` is right in `src/`, and which shape it takes. Create `formats/<format>.md` or `topics/<topic>.md` with the first change that needs it; do not add empty directories |
| [Design rules](design-rules.md) | The maintainer's recurring rulings as rules: principles, how to weigh consistency against the official tool, and what still needs a decision. Read before asking a design question |
| [Threat model](threat-model.md) | Attackers, trust boundaries, defended properties, accepted non-guarantees, open design gaps |
| [Open issues (gotchas triage)](open-issues.md) | Fixable leftovers vs irreducible user gotchas; docs/spec drift |
| [Compression-library analysis](library-analysis.md) | Per-codec backend choice and rationale |
| [Known issues](known-issues.md) | Live archivey defects and live upstream bugs archivey works around: symptom, what archivey does, what remains, and a link to the evidence |
| [Release checklist](release-checklist.md) | Every-release loop: CHANGELOG, perf vs prior tag, docs, tag/publish |
| [Release-repo cutover](release-repo-cutover.md) | One-time rename / PyPI / Pages before the first public tag |
| [Decision log](decisions/index.md) | Rare repo-wide ADRs; prefer light notes on format/topic handbook pages for new decisions |
| [Investigations](investigations/) | Finished evidence: PPMd, pyppmd/rapidgzip/pybcj/py7zr upstream reports, parallel-reader, [`alternative RAR decompressors`](investigations/alternative-rar-decompressors.md), [`capability declaration vs behaviour`](investigations/capability-declaration-vs-behaviour.md), [`writer timestamp slots`](investigations/writer-timestamp-slots.md) |
| [Discussions](discussions/) | Design questions written for circulation. Includes [pair-workflow adoption](discussions/2026-09-pair-workflow-adoption.md) and [specs → handbook + tests](discussions/2026-09-specs-to-handbook-and-tests.md) (thin-as-you-go) |
| [History](history/index.md) | Superseded prose (SPEC / ARCHITECTURE / COMPARISON / ASYNC / the pre-0.2.0 PLAN) |
| [IDEAS.md](IDEAS.md) | Speculative backlog. Committed work is the open OpenSpec changes under `openspec/changes/` |

**Maintainer reading surface:** pair workflow + handbook pages above.
`openspec/specs/` remain the **authoritative contract** for agents/CI, not the primary
human UI. Product framing: `VISION.md` at the repository root.
