# format-rar — materialization becomes bounded and reported

## MODIFIED Requirements

### Requirement: Serve random access and extraction with bounded explicit temp use

The system SHALL serve non-solid random reads by invoking `unrar` for the target
member **with that member's path as the sole path argument**, doing O(member_size)
data work. For solid random reads, the system SHALL decode from archive start to
the target member (named `unrar p … <member>`). Each such read is its own
decode: the reader SHALL NOT amortize repeated solid reads by extracting members
into a temporary directory and serving later reads from disk. `extract_all()`
SHALL be served by the same `stream_members()` pass as any other caller, plus a
second pass for hardlink sources the selector excluded; on a solid archive each
pass is one unnamed `unrar p` pipe over the whole archive. Which members a pass
names on the `unrar` command line — and which need no spawn at all — is governed
by `Constrain unrar argv by call site`. Any temp materialization SHALL be a declared RAR strategy, not an implicit
in-memory buffer; the one the reader declares is copying a non-path archive
*source* to disk so `unrar` can seek it.

**Materializing the archive source is subject to the configured spool limit**
(`access-mode-and-cost`) and SHALL NOT be exempt from it on the grounds that the source
was already seekable: the bytes, the directory and the cost are the same either way. A
source larger than the limit SHALL raise `SpoolLimitExceededError` before any bytes are
written, since the archive size is known. With the limit set to none, a member that
cannot be read directly SHALL be refused rather than materialized. Multi-volume stream
sources SHALL be measured **across the whole volume set**, not per volume.

When the archive is opened from a
non-path stream source, `ar.cost.notes` SHALL include a human-readable disk-copy
caveat **at open** (path sources SHALL NOT): a single stream source SHALL warn
that reading a compressed member will copy the whole archive to disk; ordered
stream volumes SHALL state that volumes were copied at open. The caveat SHALL name
the limit that bounds the copy, so a caller reads the worst case rather than only the
fact of it. The note is a
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
| Stream source, archive within the spool limit | One materialization; the open-time caveat already named the bound |
| Stream source, archive over the spool limit | `SpoolLimitExceededError` before any bytes are written |
| Stream source, spool limit set to none, compressed member | Refused; nothing is written |
| Stream source, second compressed member after the first | No second spool; materialization is once per reader |
| Multi-volume stream source | One spool set; the limit applies to the total across volumes |
| Repeated random opens in solid RAR | Each open is its own `unrar p` decode from archive start; no tempdir cache, and the re-decode is reported as `RewindWarning.min_redecode_bytes` |
| `extract_all()` | The same `stream_members()` pass as any other caller, plus a second pass for hardlink sources the selector excluded; no `unrar x` |
| Mixed-password nonsolid stream/open | Per-member named `unrar` (or equivalent); no ALL-pipe demux |
| Single non-path stream, at open | `ar.cost.notes` warns a compressed read will copy to disk, naming the limit that bounds it |
| Ordered stream volumes, at open | `ar.cost.notes` states volumes were copied at open |
| Path source | `ar.cost.notes` has no disk-copy caveat |
