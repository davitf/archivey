## ADDED Requirements

### Requirement: Refuse a UDIF disk image by name

The system SHALL report a UDIF image (`koly` block at offset 0 or at the start of the
last 512 bytes) as `ArchiveFormat.DMG` / `CERTAIN` / `magic`, and `open_archive` SHALL
raise `UnsupportedFeatureError` naming UDIF. `format_availability(DMG)` SHALL be `NONE`
with an empty `missing`: known, not supported, nothing to install. The `.dmg` suffix
SHALL NOT select the format.

#### Scenario: UDIF matrix

| Case | Expected |
| --- | --- |
| Seekable image, zlib, bzip2 or xz first block, `koly` trailer | `DMG` / `CERTAIN` / `magic`; `open_archive` raises `UnsupportedFeatureError` naming UDIF |
| `koly` block at offset 0 | `DMG` / `CERTAIN` / `magic`; the same refusal |
| Real zlib, bzip2 or xz stream, no `koly` block | Unchanged |
| Version other than 4 | Not `DMG` |
| ZIP whose name ends in `.dmg` | `ZIP` |
| `format=ZLIB` on a zlib-first image | The first block is read; detection does not run |
| `format=DMG` or `format="dmg"` | `UnsupportedFeatureError` naming UDIF |
| Non-seekable source longer than the far-magic window, zlib first block, `koly` at the end | `ZLIB`; the trailer is not seeked to |
| `format_availability(DMG)` | `NONE`, `missing` empty; in `list_known_formats()`, absent from `list_supported_formats()` |

## MODIFIED Requirements

### Requirement: Magic-first detection with extension fallback and confidence scoring

The system SHALL execute format detection with this algorithm:

1. Read up to `DETECTION_LIMIT` bytes (default 4096) from the source.
2. **Near magic** — the magic-byte table at exact offsets within that window. Match →
   `CERTAIN` / `detected_by="magic"`.
3. **Prefixed payload** — the tiers owned by *Self-extracting (SFX) archives are detected
   behind an executable stub*.
4. **Far magic** — signatures whose end offset lies outside the default window, today ISO
   9660's `CD001` at 32 769. Match → `CERTAIN` / `detected_by="magic"`. This SHALL be
   attempted **before** the content probes: it is exact magic at a known offset and they
   are the weakest signal available. It SHALL be skipped when the source size is known to
   be smaller than the extended window, and a source too short for it SHALL fall through
   rather than be rejected.
5. **Trailer magic** — exact magic at the start of a fixed-length block at the end of the
   source, today UDIF's `koly` block (512 bytes). Match → `CERTAIN` /
   `detected_by="magic"`. This SHALL run after far magic and before the content probes.
   It SHALL also outrank a near-magic hit whose format the trailer lists in `preempts`
   (today bzip2 and xz): that hit is one block of the image, and the replacement SHALL
   happen before an inner-TAR upgrade. The read SHALL be a cheap seek that restores the
   handle. A source that cannot seek cheaply SHALL skip it, except that a tail already
   held in the detection prefix still matches. A source shorter than the block SHALL NOT
   be read for it.
6. **Content probes** — formats with no exact magic. Match → `detected_by="content_probe"`.
7. **Extension** — `Path` with a known extension → `GUESS` / `detected_by="extension"`.
8. `FormatDetectionError` when nothing matched.

Steps are ordered attempts, not alternatives: a step that produces no match falls through,
and attempting one never prevents a later one from running.

**Tie rule.** When more than one format could match, the earlier step wins, and within a
step the earlier entry in registry order wins. This is the documented rule, not an
accident of iteration: `confidence` is a provisional grade and `detected_by` an open set,
so a later release may grade evidence more finely without breaking a caller that treats
unknown values as possible. A source with no bytes left at its current position (empty,
or already read to its end) SHALL raise `FormatDetectionError` saying there are no bytes
to read, not that nothing matched.

#### Scenario: unrecognised bytes, no path

| Case | Expected |
| --- | --- |
| Non-seekable `BinaryIO`, no filename, no magic | `FormatDetectionError` |

#### Scenario: far magic precedes the content probes

| Case | Expected |
| --- | --- |
| Bootable/hybrid ISO whose 32 KiB system area holds boot code a probe accepts | `ISO` / `CERTAIN` / `magic` — not a fabricated single-file member |
| ISO with a zeroed system area | `ISO` / `CERTAIN` / `magic`; unchanged |
| Source smaller than the extended window, size known | Step 4 skipped without an extended peek; falls through |
| Source too short for the window, size unknown | Short peek, no match, falls through — never an error for being short |
| Real Brotli stream larger than the window, no extension | One bounded peek misses at step 4, then step 6 detects it |

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
| UDIF | `6B 6F 6C 79 00 00 00 04 00 00 02 00` (`koly`, version 4, header size 512) at offset 0, or at the start of the last 512 bytes |
| TAR | `ustar` at 257 |
| LZ4 | `04 22 4D 18` (frame); `02 21 4C 18` (legacy stream, `lz4 -l`) |
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
| `koly` block at offset 0, or at the start of the last 512 bytes | `DMG`, `CERTAIN`, `magic`; opening it raises `UnsupportedFeatureError` |
| Starts `02 21 4C 18` (legacy LZ4) | LZ4, `CERTAIN`, `magic` |

#### Scenario: zstd frame prefix

| Case | Expected |
| --- | --- |
| Regular frame at offset 0 | `ZST`, `CERTAIN`, `magic` |
| One skippable frame, then a regular frame | `ZST`, `CERTAIN`, `magic` |
| Several chained skippable frames, then a regular frame | `ZST`, `CERTAIN`, `magic` |
| Skippable frames only, no regular frame | No zstd claim; falls through |
| Skippable frame whose declared size runs past the peeked prefix | No zstd claim; no extended read |
