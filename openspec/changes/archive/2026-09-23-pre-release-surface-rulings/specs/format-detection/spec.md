## MODIFIED Requirements

### Requirement: Magic-byte table

Exact matches only (no fuzzy/weak magic). Recognised:

| Format | Signature (summary) |
| --- | --- |
| ZIP | `50 4B 03 04` / `07 08` / `05 06` |
| GZip | `1F 8B` |
| BZip2 | `42 5A 68` |
| XZ | `FD 37 7A 58 5A 00` |
| Zstandard | `28 B5 2F FD`, optionally preceded by skippable frames |
| 7-Zip | `37 7A BC AF 27 1C` |
| RAR 4.x / 5.x | `52 61 72 21 1A 07 00` / `… 01 00` |
| ISO 9660 | `CD001` at 32769 |
| ISO 9660, raw CD sector image | `00 FF×10 00` sector sync at 0 (claimed so `format-iso` can refuse it by name) |
| TAR | `ustar` at 257 |
| LZ4 | `04 22 4D 18` |
| lzip | `LZIP` |
| unix-compress | `1F 9D` |

Formats without reliable exact magic (notably **zlib**) SHALL NOT appear here —
content probe only.

A zstd **skippable frame** — magic in `0x184D2A50 .. 0x184D2A5F` followed by a
little-endian `uint32` payload size — carries no compressed data and MAY precede the first
regular frame. Detection SHALL walk consecutive skippable frames by their declared sizes
within the peeked prefix and match the regular frame that follows. A source of skippable
frames alone, or one whose declared size runs past the prefix, SHALL NOT be claimed as
zstd: the walk is arithmetic over already-peeked bytes and never extends the read.

#### Scenario: magic matrix

| Case | Expected |
| --- | --- |
| Starts `50 4B 03 04` | ZIP, `CERTAIN`, `magic` |
| Magic table consulted for zlib | No zlib entry; CMF/FLG → zlib probe |
| `ustar` at 257, ≥512 bytes | TAR, `CERTAIN`, `magic` |
| Raw CD sector sync at 0 | ISO, `CERTAIN`, `magic`; opening it raises `UnsupportedFeatureError` |

#### Scenario: zstd frame prefix

| Case | Expected |
| --- | --- |
| Regular frame at offset 0 | `ZST`, `CERTAIN`, `magic` |
| One skippable frame, then a regular frame | `ZST`, `CERTAIN`, `magic` |
| Several chained skippable frames, then a regular frame | `ZST`, `CERTAIN`, `magic` |
| Skippable frames only, no regular frame | No zstd claim; falls through |
| Skippable frame whose declared size runs past the peeked prefix | No zstd claim; no extended read |
