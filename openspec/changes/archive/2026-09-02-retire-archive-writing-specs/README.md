# retire-archive-writing-specs

Remove the `archive-writing` capability and the TAR/testing write requirements. The
design moves to `dev-docs/investigations/archive-writing-design.md`.

## Why

`archive-writing` specified `archivey.create()`, `ArchiveWriter`, four `add_*` methods,
streaming conversion via `add_members()`, and the `CompressionSpec` model — five
requirements, none implemented. There is no writer in `src/` in any form, and writing is
`PLAN.md` phase 9, explicitly not a 1.0 requirement. A capability spec is meant to record
the contract that ships; this one claimed one that does not exist.

`format-tar` and `testing-contract` carried the matching claims: "SHALL support writing
TAR archives, including streaming writes", and a round-trip test required for every
writable format — of which there are none, now that `format-zip`'s streaming-write
requirement has gone (`2026-09-02-drop-unshipped-write-claims`).

## Why the capability is deleted by hand rather than by a delta

OpenSpec has no way to retire a capability. A `## REMOVED Requirements` delta covering
every requirement leaves a spec with none, which `openspec archive` refuses to write
("Spec must have at least one requirement"); deleting the directory first makes archive
treat the same delta as a request to *create* the capability. So `openspec/specs/archive-writing/`
is removed by hand and this change carries only the two deltas that modify surviving
specs. The five retired requirements and their reasons:

| Requirement | Reason |
| --- | --- |
| Creating an archive for writing | Specified `archivey.create()` and the `ArchiveWriter` lifecycle. Unimplemented |
| Adding entries from the filesystem | Specified `add_file()`. Unimplemented |
| Adding entries from bytes, streams, or members | Specified `add_bytes()` / `add_stream()` / `add_member()`. Unimplemented |
| Streaming conversion via add_members | Specified `add_members()` and the reader-to-writer conversion contract. Unimplemented |
| CompressionSpec model and convenience constants | Specified the dataclass, `CompressionLevel`, the constants and the algorithm/level resolution matrix. Unimplemented |

## Where the design went

`dev-docs/investigations/archive-writing-design.md` carries all of it: the writer
surface, the `add_members` conversion semantics including the filter-on-a-copy rule that
keeps late-bound member fields visible, the `CompressionSpec` resolution matrix and its
no-silent-substitution rule, the removed ZIP and TAR write requirements, the round-trip
testing requirement verbatim, the two questions the spec never resolved, and the
reproducible-output / metadata-fidelity explorations that phase 9 makes an entry gate.

Re-specify from there when writing starts. Do not restore the capability as-is: it
predates both explorations, and `PLAN.md` says they shape the API and are costly to
retrofit.
