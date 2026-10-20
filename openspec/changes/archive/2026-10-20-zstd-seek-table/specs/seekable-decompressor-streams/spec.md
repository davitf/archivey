## ADDED Requirements

### Requirement: zstd frame starts are seek points

On declared seek demand, every regular zstd frame after the first SHALL be a seek
point, recorded when a forward read reaches it. A skippable frame holds no output and
SHALL NOT be one. A stream opened without seek demand, or a container coder that is one
frame (`single_stream`), SHALL keep no points.

#### Scenario: frame point matrix

| Case | Expected |
| --- | --- |
| Multi-frame file, read forward, then seek back | Resumes at the frame start before the target |
| Skippable frame before each frame (pzstd) | Points at the regular frames only |
| Stream opened without seek demand | No points kept, no table read |

### Requirement: zstd reads the Seekable Zstandard seek table

A forward seek past the known points, a `SEEK_END`, or a size query SHALL read the
*Seekable Zstandard* seek table when the source ends with one (skippable magic
`0x184D2A5E`, footer magic `0x8F92EAB1`). Its frame sizes SHALL give the seek points and
the total size, with no decoding. The table is trusted like the xz and lzip indexes
(threat-model O17); its per-frame XXH64 checksums SHALL NOT be checked. A table over the
seek-table cap SHALL be thinned and keep the exact total.

#### Scenario: seek table matrix

| Case | Expected |
| --- | --- |
| `seek(0, SEEK_END)` | Total from the table; only the table is read |
| Seek into frame N | Decoding resumes at frame N's start |
| Reserved descriptor bits set, or frame sizes that do not reach the table | `SEEK_INDEX_DEGRADED` (scan `zstd_seek_table`); reads decode from the start |
| Zeros after the table | No table found, no diagnostic; reads decode from the start |
