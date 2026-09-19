# format-7z — BCJ through liblzma delta

> Replaces the pybcj staging requirement with a liblzma one, and drops `pybcj` from the
> codec/availability rows. The LZMA1+BCJ staging rule (never one combined `FORMAT_RAW`
> chain) survives unchanged — BPO-21872 is a separate defect from the pybcj limits.

## REMOVED Requirements

### Requirement: Stage LZMA1+BCJ through pybcj under `[recommended]`

**Reason:** `pybcj` cannot decode a BCJ member of 2 GiB or more (its stream size is a C
signed `int`) and its IA64 filter drops the trailing partial block. Both are reachable on
archives 7-Zip writes and reads back correctly. liblzma has neither flaw, so the branch
filters no longer need an optional package at all.

**Migration:** none for callers. Archives that decoded before decode to the same bytes;
archives that raised `OverflowError` or `TruncatedError` now read. `pip install
archivey[recommended]` no longer installs `pybcj`, and no 7z folder raises
`PackageNotInstalledError` naming it.

## ADDED Requirements

### Requirement: Decode BCJ branch filters through liblzma

The system SHALL decode every 7z BCJ branch filter (x86/ARM/ARMT/PPC/SPARC/IA64,
method IDs `0x04`-`0x09` and their long aliases) using liblzma's branch filters, in
core, for every folder shape. No optional package SHALL be required for BCJ.

A BCJ coder that sits inside an LZMA2 filter chain SHALL be folded into that chain.
A BCJ coder staged on its own — after LZMA1, after a non-LZMA codec, or alone —
SHALL run as a raw liblzma chain of that branch filter followed by `FILTER_LZMA2`,
with its input framed as LZMA2 *uncompressed* chunks; liblzma rejects a raw chain
whose only filter is a branch filter, and uncompressed chunks supply the required
trailing compression filter without compressing anything.

The coder's declared unpack size SHALL NOT be passed to the branch filter. It
determines only whether the stream finished, so a member of 2 GiB or more decodes
like any other, and a member whose length is not a whole number of the filter's
blocks SHALL return its trailing partial block.

LZMA1+BCJ folders SHALL still NOT be decoded via a single combined `lzma`
`FORMAT_RAW` filter chain: liblzma can silently truncate the final BCJ look-ahead
bytes when LZMA1 lacks an end-of-stream marker (BPO-21872). The reader MUST stage
LZMA1 (and any non-BCJ `lzma` filters such as Delta) through stdlib `lzma`, then
apply each BCJ stage separately. BCJ2 (`0x0303011B`) remains unsupported.

#### Scenario: BCJ decode matrix

| Case | Expected |
| --- | --- |
| BCJ + LZMA2 folder | One shared `lzma` raw filter chain returns original bytes |
| 7-Zip CLI `-m0=BCJ -m1=LZMA` fixture | Staged LZMA1 then a staged BCJ returns original bytes; no silent truncation |
| py7zr `FILTER_X86`+`FILTER_LZMA` fixture | Round-trip bytes match |
| BCJ with PPMd, BZip2, Deflate or Copy | Codec stage then a staged BCJ returns original bytes |
| Any BCJ folder with `bcj` unimportable | Decodes normally; no `PackageNotInstalledError` |
| BCJ member of 2 GiB or more | Decodes; no `OverflowError` reaches the caller |
| IA64 member whose length is not a multiple of 16 | Trailing partial block returned; no `TruncatedError` |

## MODIFIED Requirements

### Requirement: Decode folder coder chains through compressed-streams

The system SHALL decode each folder by composing shared `compressed-streams`
backends in decoding order. A coder list such as `AES -> LZMA2` decrypts, then
decompresses. Files in a folder are yielded by reading exactly `member.size`
bytes in archive order from the decompressed folder stream. Per-member CRC32
values SHALL appear in `hashes["crc32"]` and SHALL be verified by the shared
verification stage as data is read.

| 7z codec | Method ID | Backend | Availability |
| --- | --- | --- | --- |
| STORED | `0x00` | pass-through | core |
| LZMA1 / LZMA2 | `0x030101` / `0x21` | `lzma` `FORMAT_RAW` | core |
| Delta | `0x03` | `lzma.FILTER_DELTA` | core |
| BCJ x86/ARM/ARMT/PPC/SPARC/IA64 | `0x04`-`0x09`, `0x03030103`... | `lzma` BCJ filters | core |
| Deflate | `0x040108` | raw `zlib` | core |
| BZip2 | `0x040202` | `bz2` | core |
| Zstd | `0x04f71101` | stdlib `compression.zstd` / `backports.zstd` | core on 3.14+; otherwise `[recommended]` |
| Brotli | `0x04f71102` | `brotli` | `[recommended]` |
| LZ4 | `0x04f71104` | `lz4` frame decoder (same backend as standalone / `.tar.lz4`) | `[recommended]` |
| PPMd (var.H) | `0x030401` | `pyppmd` | `[recommended]` |
| Deflate64 | `0x040109` | `inflate64` | `[recommended]` |
| AES-256 / SHA-256 | `0x06f10701` | crypto backend | `[recommended]` |
| BCJ2 | `0x0303011B` | none | unsupported |

The `[recommended]` extra SHALL provide PPMd, Deflate64, Zstd on Python versions without
stdlib zstd, Brotli, LZ4, and AES support in one install.

LZMA1+BCJ folders SHALL NOT be decoded via a single combined `lzma` `FORMAT_RAW`
filter chain: liblzma can silently truncate the final BCJ look-ahead bytes when
LZMA1 lacks an end-of-stream marker. The reader MUST stage LZMA1 (and any non-BCJ
`lzma` filters such as Delta) through stdlib `lzma`, then apply each BCJ stage
separately. LZMA2+BCJ remains a single stdlib filter chain.

#### Scenario: coder-chain matrix

| Case | Expected |
| --- | --- |
| BCJ + LZMA2 folder | Shared `lzma` raw filter chain returns original bytes |
| BCJ + LZMA1 folder | Staged LZMA1 then a staged liblzma BCJ returns original bytes |
| Member with stored CRC32 | Terminal verification raises `CorruptionError` on mismatch |
| PPMd without `pyppmd` | `PackageNotInstalledError` names `pyppmd` and the `[recommended]` extra |
| AES + LZMA2 folder | Crypto stage decrypts before LZMA2 decompression |
| LZ4 folder (`0x04f71104`) with `lz4` installed | Shared `Codec.LZ4` returns original bytes |
| LZ4 folder without `lz4` | `PackageNotInstalledError` names `lz4` and the `[recommended]` extra |

### Requirement: Reject unsupported codecs without fallback

The system SHALL raise `UnsupportedFeatureError` naming the codec or method ID
when a folder uses a coder with no available backend. This includes BCJ2, newer
branch filters absent from installed liblzma, and unrecognized method IDs. The
reader MUST NOT return garbage and MUST NOT fall back to `py7zr` or another
third-party reader. PPMd and Deflate64 are optional-supported via
`[recommended]`, and multi-volume 7z is supported by volume joining.

#### Scenario: unsupported-codec matrix

| Case | Expected |
| --- | --- |
| Folder uses BCJ2 | `UnsupportedFeatureError` names BCJ2; no output bytes |
| Folder uses unknown method ID | `UnsupportedFeatureError` names the method ID |
| Folder uses PPMd with `pyppmd` installed | Member is decoded, not rejected |
| Folder uses LZMA1+BCJ | Member is decoded via a staged BCJ filter, not rejected |
