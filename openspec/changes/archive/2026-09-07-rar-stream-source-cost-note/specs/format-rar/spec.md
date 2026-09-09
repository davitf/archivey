## MODIFIED Requirements

### Requirement: Serve random access and extraction with bounded explicit temp use

The system SHALL serve non-solid random reads by invoking `unrar` for the target
member **with that member's path as the sole path argument**, doing O(member_size)
data work. For solid random reads, the system SHALL decode from archive start to
the target member (named `unrar p … <member>`) or extract once with `unrar x`
into an explicitly managed temporary directory and serve later reads from disk;
that directory is cleaned up on reader close. `extract_all()` MAY use one
`unrar x` to a temporary directory. Any temp materialization SHALL be a declared
RAR strategy, not an implicit in-memory buffer. When the archive is opened from a
non-path stream source, `ar.cost.notes` SHALL include a human-readable caveat **at
open** that reading a compressed member will copy the whole archive to disk (path
sources SHALL NOT). The note is a static open-time caveat, not an occurrence log:
it SHALL be present even if only stored members are read, and SHALL NOT appear
after materialization if it was absent at open. Mixed-password
nonsolid archives MUST NOT demultiplex one unnamed `unrar p` ALL pipe against the
full member list (wrong-password members are omitted from stdout and would
desynchronize sizes).

#### Scenario: random/extract matrix

| Case | Expected |
| --- | --- |
| Random `open()` in non-solid RAR | `unrar p … <archive> <member>`; work is O(member_size) |
| Repeated random opens in solid RAR | Backend may use one tempdir extraction and remove it on close |
| `extract_all()` | Backend may use one-shot `unrar x` |
| Mixed-password nonsolid stream/open | Per-member named `unrar` (or equivalent); no ALL-pipe demux |
| Stream source (non-path), at open | `ar.cost.notes` carries a disk-copy caveat |
| Path source | `ar.cost.notes` has no disk-copy caveat |
