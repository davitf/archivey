# What a scan of a real backup drive found

**Status:** three defects reproduced with synthetic inputs and pinned as `xfail(strict=True)`
in `tests/test_audit_backup_scan.py`; one of them needs a maintainer decision (§3.3); the rest wait
for the original files (§4, §5). The scan script's decoder-memory column is fixed (§2).

## 1. The scan

`scripts/scan_archives.py` dry-ran every file that `detect_format` placed on a personal
backup drive: years of unsorted copies of home directories, music and downloads. It
produced **57 390 archive rows**. File paths and names are left out of this record; only
counts and file types are kept.

| Format | Rows | Format | Rows |
| --- | ---: | --- | ---: |
| `raw_stream.zz` (zlib) | 26 821 | `raw_stream.bz2` | 1 084 |
| `zip` | 12 734 | `rar` | 981 |
| `raw_stream.xz` | 5 550 | `raw_stream.br` | 437 |
| `tar.bz2` | 3 594 | `tar.gz` | 371 |
| `raw_stream.gz` | 2 672 | `7z` | 231 |
| `tar.xz` | 2 666 | `iso` | 144 |

The log was triaged by grouping messages into templates (numbers and quoted names
replaced), then sampling each group. **No bug flag was raised**: no exception other than
an `ArchiveyError` left the library across all 57 390 archives.

## 2. The decoder-memory column was measuring detection

The summary reported 24 083 archives near `max_decoder_memory` and 2 666 over it. **All of
them came from detection, not decoding.** The content probes open the LZMA Alone codec on
every file that no magic places, and the codec checks header bytes 1-4 as a dictionary
size. The script's probe counted each check as a declared allocation:

- 26 681 of the zlib rows are git loose objects (extensionless files under
  `.git/objects/`). Their zlib header and first deflate bytes read as 1.0-3.4 GB.
- OLE compound files (`Thumbs.db`, `.doc`, `.msi`) read `cf11e0a1` as 2.7 GB.

The script now ignores the check while `_detect_format_body` runs.
`test_detection_probes_do_not_count_as_decoder_memory` fails on the old script
(`'3385478044' == ''`) and passes on the new one. A real `.lzma` still reports its
8 MiB dictionary.

A rescan is needed to see the real decoder-memory distribution: the noise covered all of it.

## 3. Defects

### 3.1 The LZMA Alone probe accepts a header followed by a zero run (55 files)

55 files were detected as `raw_stream.lzma` with PROBABLE confidence. None decoded. They
were MP3s, plus some `.jar` and TensorFlow `.data` files. For the MP3s, the logged
dictionary sizes are the ID3 tag bytes: `209 732` is `44 33 03 00`, that is `D3` and
version 3.0, the bytes after `ID3`.

The mechanism: a range coder fed zero bytes decodes an endless run of zero literals
without error. Any 13-byte prefix that passes the header gate and is followed by zeros is
therefore accepted. An ID3v2.3 tag that begins with padding is such a prefix. So is the
OLE magic followed by zeros (`0xD0` is legal properties: lc=1, lp=3, pb=4).

The `format-detection` spec keeps the Alone probe at PROBABLE because it "measured **0
false positives in 20 000 random blobs**", and says that the probe's real-world residual
was 13-byte files only. Random blobs never contain zero runs, and real files often do.
That measurement does not cover this class.

Pinned: `test_id3_tagged_mp3_is_not_lzma_alone`,
`test_ole_magic_then_zeros_is_not_lzma_alone`, and the mechanism itself in
`test_zero_run_after_an_alone_header_decodes_without_error`.

### 3.2 Brotli on OLE files: the P12 residual (437 files)

437 files were detected as `raw_stream.br`, 429 of them at GUESS confidence. They were
`.doc`, `.db` (`Thumbs.db`), `.msi`, `.ppt`, `.xls`, AppleDouble `._*` files and some
images. All of them failed to decode except two. This is the residual that
`open-issues.md` P12 tracks. The scan adds a real-world measure of it, and a
reproducible input: the 32-byte header that every version-3 OLE file starts with,
followed by zeros, at 256 KiB.

Pinned: `test_ole_header_then_zeros_is_not_brotli`. P12 and the spec forbid fixing it with
a threshold. A fix needs a check that comes from Brotli's own framing, or a recognized
non-archive signature that stops the probes, as the executable cue does for MZ and ELF.

### 3.3 ZIP: local-header name differs from the central directory (decision)

In 3 archives (two of them copies of one), the central directory and the local header
spell a member's name in different code pages. For example, the central copy holds `0xF4`
(ô in Latin-1) and the local copy `0x93` (ô in cp437), and neither copy sets the UTF-8
flag. `ZipReader._local_data_region` refuses such a member, as stdlib `zipfile` does.
`unzip` prints "mismatching local filename, continuing with central filename version" and
extracts the member. 7-Zip extracts it without a message.

Archivey takes every name from the central directory, so the local copy does not decide
anything else. One option is to read the member and record a diagnostic. This needs a
decision, because `dev-docs/formats/zip.md` documents the comparison. A name mismatch is
also a known spoofing shape for tools that trust the local header.

Pinned: `test_local_name_in_another_codepage_still_reads`.

## 4. ZIP local headers not where the central directory says

`ZipReader._local_data_region` refused 4 183 members with a stdlib-style `BadZipFile`. The
name mismatch of §3.3 accounts for 4 of them. The rest:

| Message | Members | Archives |
| --- | ---: | ---: |
| Bad magic number for file header | 3 999 | 7 |
| Overlapped entries (possible zip bomb) | 178 | 2 |
| Absurd local-header offset: **negative** | 2 | 2 |

**One ZIP over 4 GiB written without ZIP64** accounts for 4 158 of these failures: 2 122
members, in two copies of the archive. Its central-directory offsets are the true offsets
mod 2³². A wrapped offset lands in the middle of other data (bad magic) or makes two
entries appear to overlap. No tool the owner tried can salvage it. A reader that walks
local headers forward, or one that tries `offset + k·2³²` until it finds a local header
with the right name and CRC, could recover it. An unfinished attempt at the second
approach exists in the earlier codebase. The producer is unknown. A lead, not yet
verified: `java.util.zip` before JDK 7 had no ZIP64 support. The file can be made
available. Reproducing it needs an archive over 4 GiB, so a test would build a sparse
file or patch the offsets of a small one.

The others need their files (§5): a 120 MB ZIP in which all 13 members fail with bad
magic (data prepended without adjusted offsets would look like this), two small ZIPs
with a negative local-header offset (archivey's own prefix adjustment may go below zero),
a 90 MB ZIP with one overlapped entry, and installer `.exe` and `.part` files whose ZIP signature may be coincidental.

## 5. Waiting for the original files

| Group | Rows | What to run |
| --- | ---: | --- |
| ZIP: all members "Bad magic number"; negative local-header offset | 1; 2 | `unzip -t` (look for "extra bytes at beginning"), `zipinfo -v` |
| ZIP "central directory unreadable" | 92 | `unzip -l`, `7z l`: if they list members, local-header recovery is worth having |
| RAR "solid stream ended before the requested member" | 1 archive, 928 members | `unrar t` |
| Digest mismatch / "content ended after N of M bytes" | 23 / 10 | `unzip -t`, `unrar t` |
| "Not a RAR archive: no signature within the SFX window" | 6 | most are AppleDouble `._*.rar`; `unrar t` on the others |
| ISO "Expected at least 2 UDF Anchors", "no PVD" | 4 | `7z l` |
| "Destination already exists" | ~60 | case-only pairs are policy; confirm none are NFC/NFD duplicates |

## 6. Policy and detection observations (no defect)

- **Renaming.** 21 765 of 21 774 `member_name_normalized` warnings only strip `./`, a
  leading `/`, or a trailing `/`. The exception is a Google Takeout ZIP that records DOS
  as the creating system (a DOS-family system, by how archivey treats it) and uses the `\o/` emoticon in names. Its backslash becomes a
  separator, as `zip_reader.py` intends for DOS-created entries.
- **Unflagged UTF-8 ZIP names.** 11 839 names were read as UTF-8 without the flag. None
  looked garbled.
- **Colons.** About 12 000 entries were blocked as NTFS alternate data streams. They came
  from `wget` mirrors (`?i=http:`) and freedesktop icon names (`mime-audio:x-wav.png`).
  All are valid names on POSIX.
- **DMG.** 138 `.dmg` files were detected as raw zlib. 111 of them write 512 bytes and
  report `ok`, because only the first compressed chunk of the UDIF image decodes. All 138
  carry `archive_trailing_data`. Recognizing UDIF by its `koly` trailer and reporting it
  as unsupported would be clearer.
- **mozlz4.** Firefox `addonStartup.json.lz4` is Mozilla's `mozLz40\0` framing around a
  raw LZ4 block, not an LZ4 frame. Archivey detects it by extension only and fails it as
  unconfirmed. Reading it would be a small feature.
- **ZIP method 1 (Shrink).** One old DOS-era archive had 14 members that use it. It is
  unsupported.
- **Raw exception reprs in messages.** The ISO reader, and some gzip, tar and bzip2
  paths, put `repr(exc)` in the `CorruptionError` text (`error('unpack_from requires a
  buffer of at least 16 bytes…')`, `IndexError(…)`). The ZIP path double-escapes a byte
  string (`b'…\\\\x93…'`). The exception types are correct; only the text is affected.
- **Correct detections that looked odd.** These were checked and are right: `RS_*.xz`
  Reddit dumps that are tar (80+ members each), and gzip under `.mail`, `.inn` and `.top`
  (about 1 650 files).
