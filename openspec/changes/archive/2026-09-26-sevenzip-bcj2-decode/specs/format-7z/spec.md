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
| BCJ2 | `0x0303011B` | archivey's own decoder (pure Python) | core |

The `[recommended]` extra SHALL provide PPMd, Deflate64, Zstd on Python versions without
stdlib zstd, Brotli, LZ4, and AES support in one install.

LZMA1+BCJ folders SHALL NOT be decoded via a single combined `lzma` `FORMAT_RAW`
filter chain: liblzma can silently truncate the final BCJ look-ahead bytes when
LZMA1 lacks an end-of-stream marker. The reader MUST stage LZMA1 (and any non-BCJ
`lzma` filters such as Delta) through stdlib `lzma`, then apply each BCJ stage
separately. LZMA2+BCJ remains a single stdlib filter chain.

A folder SHALL be decoded when its coder graph is a **tree**: exactly one coder
output is left unbound (the folder's output), every other output feeds exactly one
input, every input is either bound or a packed stream, and no coder feeds itself.
Each input is decoded as its own branch — a linear chain planned by the rules
above, ending at one packed stream — so a four-input BCJ2 coder over four
branches, each with its own AES coder when the folder is encrypted, is one tree.
Every packed stream SHALL be read through its own view of the archive, with its
own position. A graph that cannot be a valid folder SHALL raise `CorruptionError`:
an input that is neither bound nor packed, an output bound to two inputs, or a
cycle. A valid graph the reader does not run SHALL raise `UnsupportedFeatureError`:
more than one unbound output, or a multi-input coder other than BCJ2. A branch's
coders SHALL be planned with that branch's own sizes, so a coder's input length is
the output length of the coder before it in the same branch.

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
| 7-Zip `-mx9` folder: `BCJ2` over `LZMA2`, `LZMA`, `LZMA` and one raw pack stream | Four branches over four packed streams return the original bytes |
| Same folder, encrypted: an AES coder on each of the four branches | Each branch decrypts, then decodes; the original bytes return |
| Coder output bound to two inputs, or a coder bound to itself | `CorruptionError`; no output bytes |
| Same malformed graph in an encrypted folder | `CorruptionError` on the first attempt; not reported as a wrong password |
| BCJ2 folder whose `main` branch is `AES` then `BZip2` | The BZip2 stage's input length is the AES coder's output, not a sibling branch's |

### Requirement: Reject unsupported codecs without fallback

The system SHALL raise `UnsupportedFeatureError` naming the codec or method ID
when a folder uses a coder with no available backend. This includes newer
branch filters absent from installed liblzma, and unrecognized method IDs. The
reader MUST NOT return garbage and MUST NOT fall back to `py7zr` or another
third-party reader. PPMd and Deflate64 are optional-supported via
`[recommended]`, and multi-volume 7z is supported by volume joining.

#### Scenario: unsupported-codec matrix

| Case | Expected |
| --- | --- |
| Folder uses BCJ2 | Member is decoded, not rejected |
| Folder uses a multi-input coder other than BCJ2 | `UnsupportedFeatureError` names the method ID |
| Folder uses unknown method ID | `UnsupportedFeatureError` names the method ID |
| Folder uses PPMd with `pyppmd` installed | Member is decoded, not rejected |
| Folder uses LZMA1+BCJ | Member is decoded via a staged BCJ filter, not rejected |

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
apply each BCJ stage separately. BCJ2 (`0x0303011B`) is not a branch filter of
this kind and does not go through liblzma; it is decoded by archivey's own
decoder.

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

## ADDED Requirements

### Requirement: Decode BCJ2 folders

The system SHALL decode the BCJ2 coder (`0x0303011B`) on a core install, with no
optional package. The coder's four inputs, in coder order, are `main`, `call`, `jump`
and the range-coder stream `rc`. Each input SHALL be decoded as its own branch of the
folder's coder tree over its own packed stream.

The decoder SHALL produce exactly the coder's declared unpack size. It SHALL copy
`main` up to and including each branch candidate (`E8`, `E9`, or `0F 80`-`0F 8F`),
decode one range-coded bit for it, and on a 1 replace the next four output bytes with
the big-endian absolute target from `call` (for `E8`) or `jump` (otherwise),
converted to the little-endian relative form `target - (position + 4)`. No bit SHALL
be decoded for a candidate that is the last output byte.

A BCJ2 member's `compression` SHALL list the BCJ2 coder, then the coders of its
`main` branch, in the pack direction the `CompressionMethod` contract states. The
coders of the `call`, `jump` and `rc` branches SHALL NOT be listed.

The BCJ2 folder stream SHALL be forward-only. A random-access `open()` of a member
decodes from the folder start, and a sequential `stream_members()` pass decodes each
folder once.

The decoder SHALL raise `TruncatedError` when any input ends before the declared
output is produced, and `CorruptionError` when `main`, `call` or `jump` still holds
bytes after the last output byte. That check SHALL read at most one byte from each
input and SHALL NOT drain an input. It SHALL NOT allocate from a declared size: output
is produced in bounded blocks, and each input is read in bounded blocks.

The LZMA decoders of a BCJ2 folder's branches run at once, so the dictionary sizes they
declare SHALL be checked together against `DecoderLimits.max_decoder_memory`, before any
branch decoder is built, and exceeding it SHALL raise `ResourceLimitError`.

#### Scenario: BCJ2 decode matrix

| Case | Expected |
| --- | --- |
| 7-Zip `-mx9` archive of an x86-64 executable | Bytes match the original file; `member.compression` is `(BCJ2, LZMA2)`: the root, then its `main` branch in the pack direction, without the `call`, `jump` and `rc` side streams |
| Solid `-mx9` folder of several executables | Every member's bytes match; `stream_members()` decodes the folder once |
| Encrypted `-mx9` BCJ2 folder with the right password | Bytes match; the password check succeeds by folder decode and CRC |
| Encrypted BCJ2 folder with a wrong password | Rejected by the password check; no bytes returned |
| Output whose last byte is `E8`, or whose last byte is `0F` | Bytes match; no range-coder bit is read for the final opcode |
| Forced BCJ2 over non-executable data | Bytes match |
| `call` stream cut short | `TruncatedError` naming the stream |
| `main` longer than the output consumes | `CorruptionError`, after reading one byte past the end, not the rest of `main` |
| `open()` of the second member of a BCJ2 folder | Bytes match; decoded from the folder start |
| BCJ2 folder whose branch dictionaries each fit `max_decoder_memory` but together do not | `ResourceLimitError`; no branch decoder is built |
