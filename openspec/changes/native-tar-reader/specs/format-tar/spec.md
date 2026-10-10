# format-tar — native TAR reader delta

> Each MODIFIED block is the full requirement as it will read after the change.

## ADDED Requirements

### Requirement: Parse TAR headers natively

The TAR backend SHALL parse headers with archivey's own parser, one header at a time
and without recursion, and SHALL NOT read through stdlib `tarfile`. It SHALL read the
encodings below, and SHALL give the same listing on every supported Python version.

| Encoding | Read |
| --- | --- |
| v7, POSIX ustar (with `prefix`), old GNU | Header fields; checksum as unsigned or signed sum |
| Numbers | Octal (NUL- or space-terminated, empty is 0) and GNU base-256 |
| PAX `x` / `X` / `g` | Length-validated records; globals persist, an empty global value deletes the key |
| GNU `L` / `K` | Long name and long link name |
| GNU sparse | Old GNU `S` with extension blocks; PAX 0.0, 0.1 and 1.0 |

#### Scenario: native parse matrix

| Case | Expected |
| --- | --- |
| The same archive on Python 3.11 to 3.15, any patch release | Same members, same bytes |
| A chain of extended headers | Read in a loop; each header is charged to the member's `max_metadata_bytes` budget before it is read |
| A sparse member | Logical bytes with holes as zeros; never bytes past the member's stored size |
| A member `seek` past its end | Returns the target; the next read returns `b""` |
| A sparse map out of order or overlapping | `UnsupportedFeatureError` |
| A rejected header after the first member | `CorruptionError` after the members before it, in both access modes |

## RENAMED Requirements

- FROM: `### Requirement: Serialize shared tarfile handle operations for concurrent reads`
- TO: `### Requirement: Serialize shared TAR handle operations for concurrent reads`

## MODIFIED Requirements

### Requirement: Serialize shared TAR handle operations for concurrent reads

For random-access TAR readers that allow concurrent member streams under
`MemberStreams.CONCURRENT`, the backend SHALL serialize every operation that touches
the shared archive handle with one per-reader lock.

The lock SHALL cover archive initialization/failure cleanup, the header walk,
strict-EOF direct reads, member stream creation, member `read` / `readinto` / `seek` /
`tell`, member close, archive close, and any operation that repositions or closes the
shared handle. The lock surrounds each complete operation, not individual raw
seek/read calls. Archivey buffering/error/lifecycle wrappers sit outside it; exception
translation, diagnostics/logging, lifecycle release, callbacks, and finalizers run after
the lock is released.

Compressed TAR remains `SOLID`; locking guarantees correctness but not parallel
throughput. Streaming TAR (`streaming=True`) remains one forward pass and does not gain
random concurrent open.

#### Scenario: TAR handle-lock matrix

| Case | Expected |
| --- | --- |
| Two file members opened and read interleaved from plain RA TAR | Each yields its exact bytes in order |
| Two file members opened and read interleaved from compressed RA TAR | Each yields exact bytes; serialization is acceptable |
| Multiple threads open/read distinct TAR members under `MemberStreams.CONCURRENT` after materialization | No data races on the shared handle |
| Materialization then strict EOF verification | The header walk and the EOF read use the same lock |
| Member operation raises/closes | Translation/logging/lifecycle/callback work runs without the TAR handle lock held |
| GNU sparse member opened | Stream yields the member's logical bytes, holes as zeros |
| `streaming=True` TAR | Forward-only contract unchanged; no concurrent random-open behavior |
| Contention on shared handle | Correctness guaranteed; no correctness speed threshold |

### Requirement: Report TAR format properties

The TAR backend SHALL expose these properties for every opened TAR archive:

| Format | Read from | Listing cost | Access cost |
| --- | --- | --- | --- |
| Plain `.tar` | The source | `REQUIRES_SCANNING` | `DIRECT` |
| `.tar.gz` | archivey's gzip decompressor | `REQUIRES_DECOMPRESSION` | `SOLID` |
| `.tar.bz2` | archivey's bzip2 decompressor | `REQUIRES_DECOMPRESSION` | `SOLID` |
| `.tar.xz` | archivey's xz decompressor | `REQUIRES_DECOMPRESSION` | `SOLID` |
| `.tar.zst` and every other codec | archivey's decompressor for it | `REQUIRES_DECOMPRESSION` | `SOLID` |
| Any of the above with `streaming=True` | The same stream, forward only | As above | As above |

TAR is read-only here: writing is not shipped for any format (`dev-docs/investigations/archive-writing-design.md`).
Compressed variants remain solid even when the source is seekable: random member
opens may re-decompress earlier bytes, while `stream_members()` is the preferred
progressive path.

#### Scenario: TAR property matrix

| Case | Expected |
| --- | --- |
| Open `TAR` | `cost.listing_cost=REQUIRES_SCANNING`; `cost.access_cost=DIRECT`; members are slices of the source |
| Open `TAR_GZ`, `TAR_BZ2`, `TAR_XZ`, or `TAR_ZST` | `cost.listing_cost=REQUIRES_DECOMPRESSION`; `cost.access_cost=SOLID`; archivey's decompressor for that codec |
| Open plain `.tar` | No decompression wrapper |
