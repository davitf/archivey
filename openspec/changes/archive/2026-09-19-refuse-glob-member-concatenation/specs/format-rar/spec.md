# format-rar — refuse a glob-named member that pulls in its siblings

## ADDED Requirements

### Requirement: Refuse a glob member name whose mask also matches earlier members

A RAR member's stored name may contain `*` or `?`. Because `unrar` is addressed by an
include mask, such a name can match other members, and `unrar` decompresses every match
and emits them concatenated ahead of the target.

When the mask built from a member's stored name also matches **earlier** payload
members, the system SHALL raise `UnsupportedFeatureError` rather than read the member.
The error SHALL name the number of bytes that would be decompressed first and the
configuration flag that allows the read, so the caller needs no source reading to
decide.

`ArchiveyConfig.rar_allow_glob_member_concatenation` SHALL default to `False`. When set
to `True` the read SHALL proceed and return the member's own bytes, skipping the earlier
matches as before. The flag SHALL govern only whether the read is attempted; it SHALL
NOT change what a successful read returns.

A glob name whose mask matches **no** other member SHALL be unaffected and SHALL read
without the flag.

A call site that builds no include mask SHALL be unaffected, whatever the member names
are. In particular a solid `stream_members()` pass uses one unnamed `unrar p` pipe
demultiplexed by size, so it SHALL read glob-named members without the flag.

The refusal exists because the extra decode is unbounded and unreported: it is not
covered by `ExtractionLimits`, which do not reach `open()` / `read()`, and
`AccessCost.DIRECT` does not predict it on a non-solid archive.

#### Scenario: glob member matrix

| Case | Expected |
| --- | --- |
| `a*.txt` with an earlier `subdir/aY.txt` match, default config | `UnsupportedFeatureError` naming the byte count and the flag |
| The same read with `rar_allow_glob_member_concatenation=True` | The member's own bytes, earlier matches skipped |
| `only*.dat`, whose mask matches nothing else, default config | Reads normally; no refusal |
| Solid `stream_members()` over glob-named members, default config | All members read; no mask is built |
| A name with no `*` or `?` | Unaffected in either configuration |
