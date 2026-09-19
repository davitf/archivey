## RENAMED Requirements

- FROM: `### Requirement: Stage LZMA1+BCJ through pybcj under `[7z]``
- TO: `### Requirement: Stage LZMA1+BCJ through pybcj under `[recommended]``

## MODIFIED Requirements

### Requirement: Declare 7-Zip format properties

The 7-Zip backend SHALL expose these properties:

| Property | Value |
| --- | --- |
| Read dependency | None; native parser + shared stdlib-backed decoders |
| Write dependency | Not shipped in the current release (writing deferred; no 7z-writing extra) |
| Listing cost | O(1); native header parse, no file-data decompression |
| Access cost | `SOLID` when any folder packs multiple files; `DIRECT` for single-file folders |
| Supports write | No (read-only until the writing phase) |
| Requires seek | Yes |

#### Scenario: format property matrix

| Case | Expected |
| --- | --- |
| Open a seekable 7z for listing | Header is parsed natively; full member list is available; no third-party reader imports |
| Open from a non-seekable source | Open fails because 7z requires seek |
| Attempt 7z write | `UnsupportedOperationError` (writing not implemented) |

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
| BCJ x86/ARM/ARMT/PPC/SPARC/IA64 | `0x04`-`0x09`, `0x03030103`... | `lzma` BCJ filters (LZMA2+BCJ); `pybcj` for LZMA1+BCJ | core for LZMA2+BCJ; `[recommended]` for LZMA1+BCJ |
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
stdlib zstd, Brotli, LZ4, AES, and LZMA1+BCJ (`pybcj`) support in one install.

LZMA1+BCJ folders SHALL NOT be decoded via a single combined `lzma` `FORMAT_RAW`
filter chain: liblzma can silently truncate the final BCJ look-ahead bytes when
LZMA1 lacks an end-of-stream marker. The reader MUST stage LZMA1 (and any non-BCJ
`lzma` filters such as Delta) through stdlib `lzma`, then apply each BCJ stage
through `pybcj`. LZMA2+BCJ remains a single stdlib filter chain in core.

#### Scenario: coder-chain matrix

| Case | Expected |
| --- | --- |
| BCJ + LZMA2 folder | Shared `lzma` raw filter chain returns original bytes |
| BCJ + LZMA1 folder with `pybcj` (`[recommended]`) | Staged LZMA1 then `pybcj` returns original bytes |
| BCJ + LZMA1 folder without `pybcj` | `PackageNotInstalledError` names `pybcj` and the `[recommended]` extra |
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
third-party reader. PPMd, Deflate64, and LZMA1+BCJ are optional-supported via
`[recommended]`, and multi-volume 7z is supported by volume joining.

#### Scenario: unsupported-codec matrix

| Case | Expected |
| --- | --- |
| Folder uses BCJ2 | `UnsupportedFeatureError` names BCJ2; no output bytes |
| Folder uses unknown method ID | `UnsupportedFeatureError` names the method ID |
| Folder uses PPMd with `pyppmd` installed | Member is decoded, not rejected |
| Folder uses LZMA1+BCJ with `pybcj` installed | Member is decoded via staged `pybcj`, not rejected |

### Requirement: Stage LZMA1+BCJ through pybcj under `[recommended]`

The system SHALL decode linear folders whose coder chain includes both LZMA1 and
at least one BCJ branch filter (x86/ARM/ARMT/PPC/SPARC/IA64) by composing
stdlib LZMA1 decompression with `pybcj` BCJ filters. The reader MUST NOT feed
LZMA1 and BCJ into one `lzma.LZMADecompressor` `FORMAT_RAW` filter list. When
`pybcj` is absent, opening such a member SHALL raise `PackageNotInstalledError`
naming `pybcj` and `pip install archivey[recommended]`. BCJ2 remains unsupported.

#### Scenario: LZMA1+BCJ matrix

| Case | Expected |
| --- | --- |
| 7-Zip CLI `-m0=BCJ -m1=LZMA` fixture + `pybcj` | Round-trip bytes match; no silent truncation |
| py7zr `FILTER_X86`+`FILTER_LZMA` fixture + `pybcj` | Round-trip bytes match |
| Same fixtures without `pybcj` | `PackageNotInstalledError` for `pybcj` / `[recommended]` |
| LZMA2+BCJ without `pybcj` | Still works in core via stdlib filters |
