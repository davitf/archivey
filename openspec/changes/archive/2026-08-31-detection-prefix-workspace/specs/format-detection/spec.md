## MODIFIED Requirements

### Requirement: Detection never consumes or discards bytes

Bytes inspected during detection MUST remain available to the backend. Wrapping
non-seekable sources is the **opener's** job so one wrapper is shared:

| Source | Behavior |
| --- | --- |
| Path / seekable stream | Peek/read then restore entry `tell()`. Archive begins where the caller positioned. `open_archive` may wrap a mid-file seekable stream in a zero-origin view (`SlicingStream`) so absolute-offset backends (e.g. ISO/`pycdlib`) see origin 0. |
| Non-seekable | `open_archive` wraps in `PeekableStream` **before** detection and passes the **same** wrapper to detection and backend. Detection uses `peek(n)` only. |

Standalone `detect_format` is non-consuming for paths/seekable streams. For a raw
non-seekable stream the caller must pass a `PeekableStream` (or equivalent) if it
will keep reading — otherwise the peeked prefix is lost. `open_archive` wraps
internally.

`PeekableStream`: buffers first `DETECTION_LIMIT` bytes (32774 when ISO triggered);
`.peek(n)` without consume; `BinaryIO` to backend (drain buffer, then underlying).

Every tier that reads from the front SHALL do so through **one detection-owned prefix
workspace** that grows monotonically: extending the window reads only the delta, and bytes
already retrieved are never re-read. A path keeps one detection handle; a seekable caller
stream records its entry position, reads forward once, and restores once in an
exception-safe exit; a non-seekable source uses the same replay buffer the backend will
consume.

#### Scenario: non-consuming matrix

| Case | Expected |
| --- | --- |
| Seekable `BinaryIO` at position N | After detect, position is N again; backend can read full archive |
| `open_archive` on non-seekable | One `PeekableStream` for detect + backend; peeked bytes replay then fall through |
| Standalone detect on raw non-seekable the caller will reread | Caller must supply `PeekableStream` |

#### Scenario: the workspace reads each byte once

| Case | Expected |
| --- | --- |
| Near magic then far magic on a seekable stream | 32 774 bytes fetched once, not 4 096 then 32 774 from zero |
| Five tiers peeking the same 30-byte head | One fetch, four buffer reads |
| Cued scan growing 64 KiB → 256 KiB → 1 MiB → 2 MiB | 2 MiB of unique source I/O, not 3.31 MiB of overlapping reads |

## ADDED Requirements

### Requirement: Detection's access shape is bounded, not only its byte count

Detection SHALL perform at most: **one forward-only pass** from the detection origin; then
at most **one seek towards the end**; then **one read to end**. No backward seek, and no
re-reading of bytes already retrieved.

This holds for every source kind. A network range reader pays at most two requests; a
member stream from a solid block decodes forward once and never rewinds into a block it has
left; a local file loses nothing. The rule is stated flatly rather than derived from a cost
model because `StreamCapability` cannot distinguish a cheap seek from an expensive one.

Resolving an exact `payload_offset` through a central-directory walk does not fit this
shape — the directory is reached backwards from the end and points backwards again. Offset
resolution is therefore separable from identification and is scoped by
`detection-evidence-ledger`.

#### Scenario: access-shape matrix

| Case | Forward reads | Seeks toward end | Backward seeks |
| --- | --- | --- | --- |
| gzip at offset 0, seekable stream | 1 pass | 0 | **0** |
| ISO far magic, seekable stream | 1 pass | 0 | **0** |
| Tail tier under `THOROUGH` | 1 pass | 1 | **0** |
| Non-seekable source, any tier | 1 pass | 0 | **0** |

### Requirement: Structural checks receive a candidate-relative view

A needle declaration SHALL carry the offset at which its magic sits **within the candidate**,
and every validator and probe SHALL receive bytes positioned at the candidate origin rather
than at the source origin.

TAR's `ustar` sits at candidate offset 257, so a scan hit at absolute offset `H` denotes a
candidate origin of `H - 257`. A gzip needle begins at candidate offset zero. Without this,
a self-extracting archive's internal structure cannot be validated at all, because the only
available view starts at the source origin.

#### Scenario: candidate-relative reads

| Case | Expected |
| --- | --- |
| `ustar` found at absolute 100 257 | Candidate origin 100 000; the 512-byte header is validated from there |
| gzip needle found at absolute 4 096 | Candidate origin 4 096; the bounded decode reads from there |
| Candidate-relative read of length N at origin O | Same bytes as an absolute read at `O` for `N` — one is a view of the other, never a second fetch |
| Candidate origin computed as negative (`H` < declared needle offset) | Not a candidate; the hit is discarded |
