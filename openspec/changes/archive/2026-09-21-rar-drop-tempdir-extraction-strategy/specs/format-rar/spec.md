# format-rar — no `unrar x` tempdir strategy

## MODIFIED Requirements

### Requirement: Serve random access and extraction with bounded explicit temp use

The system SHALL serve non-solid random reads by invoking `unrar` for the target
member **with that member's path as the sole path argument**, doing O(member_size)
data work. For solid random reads, the system SHALL decode from archive start to
the target member (named `unrar p … <member>`). Each such read is its own decode:
the reader SHALL NOT amortize repeated solid reads by extracting members into a
temporary directory and serving later reads from disk. `extract_all()` SHALL be
served by the same `stream_members()` pass as any other caller, plus a second pass
for hardlink sources the selector excluded; on a solid archive each pass is one
unnamed `unrar p` pipe over the whole archive. Which members a pass names on the
`unrar` command line — and which need no spawn at all — is governed by
`Constrain unrar argv by call site`. Any temp materialization SHALL be a declared
RAR strategy, not an implicit in-memory buffer; the only one the reader implements
is copying a non-path archive *source* to disk so `unrar` can seek it, the
deferred small-member optimization being the other strategy this capability
declares.

A non-path stream source SHALL NOT be copied to disk at open. Both stream shapes —
a single stream and an ordered set of stream volumes — SHALL defer the copy to the
first member read that `unrar` has to serve, and a caller that only lists SHALL
write nothing. An `open()` the reader refuses before spawning `unrar` — a name it
cannot address through an include mask, or one whose mask would pull in earlier
members — is not such a read and SHALL write nothing either. Listing SHALL be served from the source the caller supplied; for a
volume set the reader SHALL read each volume as its own bounded view over that
source rather than reopening or copying it.

When the copy does happen for a volume set it SHALL write the whole set, because
`unrar` resolves sibling volumes by name.

When the archive is opened from a
non-path stream source, `ar.cost.notes` SHALL include a human-readable disk-copy
caveat **at open** (path sources SHALL NOT): a single stream source SHALL warn
that reading a compressed member will copy the whole archive to disk; ordered
stream volumes SHALL warn that reading a compressed member will copy every volume
to a temp directory. The note is a
static open-time caveat, not an occurrence log:
it SHALL be present even if only stored members are read, and SHALL NOT appear
after materialization if it was absent at open. Mixed-password
nonsolid archives MUST NOT demultiplex one unnamed `unrar p` ALL pipe against the
full member list (wrong-password members are omitted from stdout and would
desynchronize sizes).

#### Scenario: random/extract matrix

| Case | Expected |
| --- | --- |
| Random `open()` in non-solid RAR | `unrar p … <archive> <member>`; work is O(member_size) |
| Repeated random opens in solid RAR | Each open is its own `unrar p` decode from archive start; no tempdir cache, and the re-decode is reported as `RewindWarning.min_redecode_bytes` |
| `extract_all()` | The same `stream_members()` pass as any other caller, plus a second pass for hardlink sources the selector excluded; no `unrar x` |
| Mixed-password nonsolid stream/open | Per-member named `unrar` (or equivalent); no ALL-pipe demux |
| Single non-path stream, at open | `ar.cost.notes` warns a compressed read will copy to disk; nothing is written yet |
| Ordered stream volumes, at open | `ar.cost.notes` warns a compressed read will copy every volume; nothing is written yet |
| Ordered stream volumes, listing only | No temp directory is created |
| Solid `stream_members()` pass, no member read | Nothing is written, even from a stream source |
| Ordered stream volumes, first compressed read | The whole set is written once; later reads reuse it; close removes it |
| Stream source, `open()` refused before any spawn | Nothing is written; the refusal raises without materializing |
| Path source | `ar.cost.notes` has no disk-copy caveat |
