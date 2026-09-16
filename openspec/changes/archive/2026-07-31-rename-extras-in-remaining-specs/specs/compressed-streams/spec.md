## MODIFIED Requirements

### Requirement: Each supported codec has a default backend

The system SHALL decompress supported codecs through these default backends:

| Codec | Default backend | Availability |
| --- | --- | --- |
| gzip | stdlib `gzip` | core |
| bzip2 | stdlib `bz2` | core |
| xz | native xz stream over stdlib `lzma` | core |
| LZMA Alone | stdlib `lzma` `FORMAT_ALONE` | core |
| LZMA1 / LZMA2 raw | stdlib `lzma` `FORMAT_RAW` | core |
| Delta, BCJ x86/ARM/ARMT/PPC/SPARC/IA64 | `lzma` raw filters | core |
| raw Deflate | stdlib `zlib` (`-15`) | core |
| Copy/STORED | pass-through | core |
| zstd | stdlib `compression.zstd` (3.14+) / `backports.zstd` (<3.14) | optional `[recommended]` before 3.14; core on 3.14+ |
| lz4 | `lz4` | optional `[recommended]` |
| Brotli | `brotli` | optional `[recommended]` |
| unix-compress `.Z` | native LZW `DecompressorStream` | core |
| PPMd var.H | `pyppmd` | optional `[recommended]` |
| Deflate64 | `inflate64` | optional `[recommended]` |
| AES-256 decrypt stage | wrapped crypto backend | optional `[recommended]` |

LZMA Alone SHALL be a distinct stream-codec descriptor from raw LZMA1/LZMA2
(`FORMAT_RAW` + properties). Alone is standalone (`StreamFormat.LZMA_ALONE`);
raw LZMA1/LZMA2 remain container-only.

#### Scenario: backend matrix

| Case | Expected |
| --- | --- |
| Default gzip stream | stdlib `gzip` |
| Default zstd on Python 3.14+ | stdlib `compression.zstd` |
| Default zstd on Python 3.11-3.13 with `backports.zstd` | `backports.zstd` using the same API |
| Standalone `.lzma` / Alone stream | `lzma` in `FORMAT_ALONE` mode |
| 7z folder LZMA2 raw stream | `lzma` in `FORMAT_RAW` mode |
| Default unix-compress `.Z` stream | native LZW stream; no `uncompresspy` import |
| Core-only install opens `.Z` | Succeeds without optional extras |

### Requirement: AES decryption is one wrapped pipeline stage

The system SHALL use `cryptography` from `[recommended]` through an internal wrapper
only. AES decryption SHALL be a stream stage composed before decompression, such
as AES then LZMA2 for an encrypted 7z folder. Format parsers MUST use the wrapper
instead of importing `cryptography` directly.

#### Scenario: crypto matrix

| Case | Expected |
| --- | --- |
| AES-encrypted 7z folder over LZMA2 with `cryptography` installed | Pipeline applies AES decrypt stage, then LZMA2 |
| Any format parser needs AES | Uses internal crypto abstraction |

### Requirement: Missing optional backends raise PackageNotInstalledError

The system SHALL raise `PackageNotInstalledError`, naming the missing package,
extra, or tool, when the selected codec/decrypt backend requires an unavailable
optional component.

#### Scenario: missing backend matrix

| Case | Expected |
| --- | --- |
| PPMd stream without `pyppmd` | `PackageNotInstalledError` naming `pyppmd` |
| AES stream without `cryptography` | `PackageNotInstalledError` naming the crypto backend |
