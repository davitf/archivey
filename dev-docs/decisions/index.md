# Decision log

Short records of **rare repo-wide** choices. Specs and handbook pages say what is true
now; these notes capture irreversible policy that does not fit a format/topic page.

**Prefer living handbook notes** for new format or topic decisions
([`../formats/`](../formats/README.md), [`../topics/`](../topics/README.md),
[`../pair-workflow.md`](../pair-workflow.md)). Add a new ADR only when the choice is
cross-cutting policy that will not live cleanly on those pages.

When you do add an ADR: `NNNN-short-slug.md` and a row in the table below. Prefer linking
to OpenSpec changes / PRs for provenance. Use `Status: placeholder` when the rationale is
not fully recovered yet.

| ID | Decision | Status |
| --- | --- | --- |
| [0001](0001-native-7z-not-py7zr.md) | Native 7z reader; `py7zr` write/oracle only | recorded |
| [0002](0002-native-rar-metadata-unrar-data.md) | Native RAR metadata; `unrar` for data | recorded |
| [0003](0003-member-streams-opt-in.md) | Default forward-only, single live stream | recorded |
| [0004](0004-streaming-bool-not-intent-enum.md) | Keep `streaming: bool`; drop `Intent` enum | recorded |
| [0005](0005-sync-only-v1.md) | Sync-only public API in v1 | recorded |
| [0006](0006-stdlib-zipfile.md) | Stdlib `zipfile` for ZIP core | recorded |
| [0007](0007-mutable-archive-member.md) | Mutable `ArchiveMember` filled in place | recorded |
| [0008](0008-single-accelerator-rapidgzip.md) | One accelerator library: `rapidgzip` | recorded |
| [0009](0009-zstd-stdlib-backports.md) | stdlib / `backports.zstd`, not `zstandard` | recorded |
| [0010](0010-no-silent-buffer-nonseekable.md) | Fail fast on non-seekable RA; no silent buffer | recorded |
| [0011](0011-zero-dependency-core.md) | Zero third-party deps in core install | recorded |
| [0012](0012-usage-errors-outside-archiveyerror.md) | `ArchiveyUsageError` ≠ `ArchiveyError` | recorded |
| [0013](0013-cross-platform-name-safety-policies.md) | Cross-platform extraction name-safety policies | recorded |
| [0014](0014-integrity-verdicts-from-reads-not-close.md) | Integrity verdicts surface from reads, never `close()` | recorded |
| [0015](0015-zero-filled-files-are-valid-empty-tars.md) | A zero-filled file is a valid empty TAR; report, never refuse | recorded |
| [0016](0016-committed-rar-corpus-fixtures.md) | RAR corpus archives are committed, pinned by a manifest | recorded |
| [0017](0017-bidi-override-rejection-is-policy-keyed.md) | Bidi-override rejection is policy-keyed; `TRUSTED` extracts | recorded |
| [0018](0018-review-and-address-stay-separate-skills.md) | Review and address stay two skills; auto-watchers route through `steward` | recorded |

Related long-form material (not ADRs):

- [Codec library analysis](../library-analysis.md)
- [Threat model + gap register](../threat-model.md)
- Maintainer vision: `VISION.md` at the repository root
- Historical comparison / architecture prose: [history](../history/index.md)
