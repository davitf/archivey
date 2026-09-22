## MODIFIED Requirements

### Requirement: Compressed streams are probed for an inner TAR

For single-file compressors (gzip, bzip2, xz, zstd, lz4, lzip, LZMA Alone, zlib,
brotli, unix-compress), detection SHALL decompress a bounded amount of *content*
and look for TAR `ustar` at offset 257, reporting combined formats (`TAR_GZ`, …)
when present. Need ≥512 decompressed bytes.

Compressed input is supplied via a **bounded, non-consuming view** (up to
`_INNER_TAR_MAX_PROBE_BYTES`, ≥ largest bzip2 first-block compressed size):

- Stream codecs pull incrementally (first few KiB usually enough).
- Block-transform (bzip2) may pull a full first block before any output.

Seekable: read + restore position. Path: open/close. Non-seekable: buffer in the
`ArchiveSource`'s replay prefix. Use sequential decompression (not random-access
accelerators that reject bounded non-seekable views). Missing decompressor → bare
compressor format; open may refine. No TAR header within the bound → bare
compressor.

#### Scenario: inner-TAR matrix

| Case | Expected |
| --- | --- |
| `.gz` → content with `ustar`@257 | `TAR_GZ` (not bare `GZIP`) |
| `.gz` → non-TAR content | `GZIP` |
| `.tar.bz2` with large first block (> peek prefix) | Read up to max block; `TAR_BZ2` |
| Large-block bare `.bz2`, no `ustar` | Bounded read; `BZ2` (no false promotion) |
| Non-seekable `.tar.bz2` needing full block | Buffered in the `ArchiveSource`'s replay prefix; `TAR_BZ2`; backend can still read all |
| Alone `.tar.lzma` / Alone `.tlz` with `ustar`@257 | `ArchiveFormat(TAR, LZMA_ALONE)` |
| Bare Alone `.lzma`, no `ustar` | `ArchiveFormat.LZMA_ALONE` |

### Requirement: Detection never consumes or discards bytes

Bytes inspected during detection MUST remain available to the backend. Wrapping
non-seekable sources is the **opener's** job so one wrapper is shared:

| Source | Behavior |
| --- | --- |
| Path / seekable stream | Peek/read then restore entry `tell()`. Archive begins where the caller positioned. `open_archive` may wrap a mid-file seekable stream in a zero-origin view (`SlicingStream`) so absolute-offset backends (e.g. ISO/`pycdlib`) see origin 0. |
| Non-seekable | The `ArchiveSource` `open_archive` builds holds a replay prefix; detection and backend receive that **same** object. Detection uses `peek(n)` only, and the backend's reads drain the prefix before reaching the source. |

Standalone `detect_format` is non-consuming for paths/seekable streams. For a raw
non-seekable stream the peeked prefix is lost to the caller unless the caller buffers
it; `open_archive` and `open_stream` keep it in the `ArchiveSource`.

The replay prefix: buffers what detection peeks, `DETECTION_LIMIT` bytes by default (32774
when ISO triggered, up to 1 MiB for the inner-TAR probe and chain walks, up to `SFX_MAX`
for the self-extracting scan); `.peek(n)` without consume; reads drain the prefix, then
the underlying source.

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
| `open_archive` on non-seekable | One `ArchiveSource` for detect + backend; peeked bytes replay then fall through |
| Standalone detect on raw non-seekable the caller will reread | Caller must buffer the stream itself |

#### Scenario: the workspace reads each byte once

| Case | Expected |
| --- | --- |
| Near magic then far magic on a seekable stream | 32 774 bytes fetched once, not 4 096 then 32 774 from zero |
| Five tiers peeking the same 30-byte head | One fetch, four buffer reads |
| Cued scan growing 64 KiB → 256 KiB → 1 MiB → 2 MiB | 2 MiB of unique source I/O, not 3.31 MiB of overlapping reads |
