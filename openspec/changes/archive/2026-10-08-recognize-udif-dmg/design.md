## Context

UDIF (Apple's disk image) ends with a 512-byte `koly` block. Old images put the same
block at offset 0. 7-Zip's check is the first 12 bytes: `koly`, version 4, header size
512, both big-endian. The block codecs include zlib, bzip2 and xz, so the first block
is a real stream of one of those.

Raw CD `.bin` images are the precedent: detection claims them so `open_archive` can
refuse them by name. UDIF is not ISO, so it needs its own format. Reading the blocks
is a separate feature.

## Goals / Non-Goals

**Goals:**

- A seekable UDIF image is `DMG`, and opening it names the image.
- A real zlib, bzip2 or xz stream is unchanged.
- A zip named `.dmg` stays a zip.

**Non-Goals:**

- Decoding UDIF blocks, XML plists, or ADC/LZFSE.
- A general ZIP tail scan.

## Decisions

### 1. Twelve bytes, not the XML range

The signature is 7-Zip's 12-byte check. Parsing the plist offset would reject images
that check accepts, for no gain: 96 bits is enough to tell the block from a compressor
trailer.

### 2. The trailer runs after far magic and before the probes

An ISO whose `CD001` already matched is not asked for a tail read, so ISO detection
cost is unchanged. A zlib-first image has no near magic, and the zlib probe is what
used to win; the trailer has to run first. Bzip2 and xz do have near magic, so those
two hits are listed on the trailer and replaced when it matches. The replacement
happens before the inner-TAR upgrade, so the image is not reported as a compressed tar.

### 3. Cheap seek only

`read_tail` seeks on a path or a plain seekable stream and restores the handle. It
does not grow the prefix through the file. A pipe and an `ArchiveStream` are not
seeked: a backward seek on an `ArchiveStream` may re-decode. When the far-magic peek
has already read a short pipe to its end, the block is in the prefix and matches
without a seek. A longer pipe stays the first block's codec.

### 4. No `.dmg` extension

The block is the claim. A zip with that name is a zip.

### 5. `READ_IMPLEMENTED` is false

Registering the format without a flag would report it `FULL` and try to read it.
Leaving it unregistered would make `format="dmg"` a usage error instead of a named
refusal. `NONE` with an empty `missing` matches `UNKNOWN`: nothing to install.
`open_archive` refuses before the registry; `reader_for_format` raises the same
`UnsupportedFeatureError` if reached.

`format=` naming a compressor skips detection, so the first block can still be read
that way. That is the existing `format=` contract. The docs do not offer it as a way
to read an image.

## Risks / Trade-offs

- [512 extra bytes on a seekable bzip2, xz, or no-near-magic source, including a
  full SFX miss] → allowed on top of the prefix/far/scan ceiling, the same way the
  Brotli probe's seeks are. ZIP, gzip and ISO return before the read.
- [A compressor whose last 512 bytes happen to start with the 12-byte block] → reported
  as `DMG`. The check is 96 bits.
- [A long UDIF image on a pipe] → still opens as its first block. Seeking a pipe to
  the end would buffer the file, which detection does not do.
