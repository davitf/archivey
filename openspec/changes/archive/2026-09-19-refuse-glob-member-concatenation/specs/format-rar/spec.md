# format-rar — refuse a glob-named member that pulls in its siblings

## ADDED Requirements

### Requirement: Refuse a glob member name whose mask also matches earlier members

A RAR member's stored name may contain `*` or `?`. Because `unrar` is addressed by an
include mask, such a name can match other members, and `unrar` decompresses every match
and emits them concatenated ahead of the target.

When the mask built from a member's stored name also matches **earlier** payload
members, the system SHALL raise `UnsupportedFeatureError` rather than read the member.
The error SHALL name the configuration flag that allows the read. On a non-solid
archive it SHALL also name the number of bytes of earlier matching members that
`unrar` would decompress first. On a solid archive those members are already inside
the solid prefix the read pays, so the error SHALL NOT describe that count as an
avoidable extra decode.

`ArchiveyConfig.rar_allow_glob_member_concatenation` SHALL default to `False`. When set
to `True` the read SHALL proceed and return the member's own bytes, skipping the earlier
matches as before. The flag SHALL govern only whether the read is attempted; it SHALL
NOT change what a successful read returns.

A glob name whose mask matches **no** other member SHALL be unaffected and SHALL read
without the flag.

A call site that builds no include mask SHALL be unaffected, whatever the member names
are. In particular a solid `stream_members()` pass uses one unnamed `unrar p` pipe
demultiplexed by size, so it SHALL read glob-named members without the flag.

The refusal exists because names like this are almost always constructed. On a
non-solid archive the extra decode is also unbounded and unreported: it is not
covered by `ExtractionLimits`, which do not reach `open()` / `read()`. On a solid
archive `AccessCost.SOLID` already advertises the prefix; the names are still
refused.

#### Scenario: glob member matrix

| Case | Expected |
| --- | --- |
| `a*.txt` with an earlier `subdir/aY.txt` match, default config, non-solid | `UnsupportedFeatureError` naming the byte count and the flag |
| The same member on a solid archive, default config | `UnsupportedFeatureError` naming the flag, not an avoidable extra decode |
| The same read with `rar_allow_glob_member_concatenation=True` | The member's own bytes, earlier matches skipped |
| `only*.dat`, whose mask matches nothing else, default config | Reads normally; no refusal |
| Solid `stream_members()` over glob-named members, default config | All members read; no mask is built |
| A name with no `*` or `?` | Unaffected in either configuration |
