# How it works

How Archivey is built, for a reader deciding whether to trust it. Each section explains
one design choice and links to the material behind it. Nothing here changes how you call
the library; the pages it links to cover that.

## Design philosophy

Archivey gives every format the same interface: one opener, one reader, one member model.
A difference between formats shows up as data, such as a `None` field or an entry in the
cost report, and not as a different API or a silent guess. The aim is that code written
against a ZIP keeps working, and keeps its cost, when someone hands it a `.tar.gz` or a
solid 7z.

That aim is why some things must be asked for. Seekable member streams
(`seekable_members=True`), several open members at once (`concurrent_members=True`) and
reading a pipe (`streaming=True`, which then refuses anything but one forward pass) are
all declared at open. Some operations stay refused even where one format could allow
them, so that a program does not depend on a property only that format has. These can
look like obstacles. Each one guards against a trap: a backward seek that quietly
decompresses the member again from the start, or a second stream that quietly decodes a
solid block again. A strict default can be relaxed in a later release without breaking
anyone; a permissive one cannot be tightened.

Everything is a stream first. A member's bytes are decoded as you read them, and a
listing does not decompress data unless the format leaves no other way. Where a format
does force it, the cost report says so.

The defaults, one by one, and what each escape hatch costs:
[Philosophy](philosophy.md) and [Access costs and pitfalls](access-and-cost.md).

## Codecs and streams

Most codecs are shared between formats: LZMA sits inside 7z, `.xz` and `.tar.xz`, and
Deflate inside ZIP, 7z and gzip. Archivey decodes all of them through one internal
stream layer. The decoders come from three places: the standard library (`lzma`, `bz2`,
`zlib`), optional packages in `[recommended]` (PPMd, Deflate64, Brotli, and Zstandard
before Python 3.14), and a few written in Python, such as the `.Z` decoder and the
framing of xz and lzip streams. AES decryption uses the `cryptography` package.

The layer makes every one of them behave like an ordinary Python stream, and the same
way in every format. Codec exceptions become
[`CorruptionError`][archivey.CorruptionError] or
[`TruncatedError`][archivey.TruncatedError]. A stored CRC or hash is checked when a
member is read to its end, and the verdict comes from `read()`, never from `close()`. A
missing codec package raises an error that names the extra to install. A stream declared
seekable with `seekable_members=True` does seek, even when that means decompressing
again from the start; where a codec allows better, the layer uses an index (xz, lzip) or
the `[seekable]` accelerator (gzip, bzip2). WinZip AES members do not seek yet; that is a
known gap, not a design choice
([Seeking inside compressed members](access-and-cost.md#seeking-inside-compressed-members)).
[`open_stream`][archivey.open_stream] exposes the layer for a bare compressed file.

Depth: the [codec library analysis](https://github.com/davitf/archivey/blob/main/dev-docs/library-analysis.md)
for which library backs each codec, and why.

## Format parsers

Format parsers come from the same three places. ZIP uses the standard library's
`zipfile` for the central directory and TAR uses `tarfile` for its headers. ISO 9660 uses
`pycdlib` from `[recommended]`. 7z and RAR headers are parsed by Archivey itself, in
Python. Whatever parses the headers, Archivey turns each format's names and metadata
into one member model by the same rules, and member data goes through the stream layer
above wherever it can. Two exceptions: ZipCrypto members, which `zipfile` decrypts and
decodes itself, and compressed RAR data, which needs RARLAB's `unrar` or `rar` because
the RAR compression format is proprietary.

7z and RAR get their own parsers for two reasons. The first is consistent metadata:
`py7zr` and `rarfile` apply their own rules to names and fields, which Archivey would
have had to copy, and sometimes undo. The second is streaming. `py7zr` writes
decompressed data into objects you give it rather than returning a stream you read
from, and turning that back into a stream took more code than parsing the headers.
`rarfile` starts one `unrar` process per member, so reading every member of a solid
archive decodes the solid block again for each one. Archivey's `stream_members()`
reads a whole solid 7z folder, or a whole solid RAR, in one forward pass. Parsing
headers in Python has a safety benefit too: a crafted header can make a parser wrong, but
cannot make it corrupt memory.

Depth: the [7z](https://github.com/davitf/archivey/blob/main/dev-docs/formats/7z.md),
[RAR](https://github.com/davitf/archivey/blob/main/dev-docs/formats/rar.md) and
[ZIP](https://github.com/davitf/archivey/blob/main/dev-docs/formats/zip.md) handbook
pages. Per-format behaviour: [Formats and extras](formats.md).

## Where the cost model comes from

Archive formats hide expensive operations, and caches and guesses can hide them further,
until a program that is fast on a ZIP turns quadratic on a `.tar.gz`. Archivey reports
the cost instead.

The report is a [`CostReceipt`][archivey.CostReceipt] on `reader.cost`, computed at open
before any member is read. It describes how listing works, whether members share a
solid block, and whether the source can seek. It never permits or refuses an access
pattern, and events at run time go to diagnostics rather than into the receipt.

What to do with it: [Access costs and pitfalls](access-and-cost.md).

## Format detection

Archivey decides what a file is from its bytes, not its name. It checks magic bytes
first, including signatures far into the file such as ISO 9660's at 32 KiB, and a stub
in front of an archive such as a self-extracting executable's. Formats with no usable
magic, such as a raw zlib or LZMA stream, are recognised by content probes that try to
parse the start. The extension only corroborates a weak probe, or serves as a last guess
when the bytes settle nothing, because a name can be wrong: a `.jpg` that is really a ZIP opens as a ZIP, and a
`FORMAT_EXTENSION_CONFLICT` diagnostic names both candidates. A compressed single file
is decompressed a little to look for a TAR header, so a `.gz` that holds a tarball opens
as `.tar.gz`. [`detect_format`][archivey.detect_format] runs the same steps without
opening the archive.

Every format registers its magic, probes and extensions with one registry, so adding a
format means writing a backend rather than changing the detector. That registry is not
a public API yet.

Details: [Opening and listing](opening-and-listing.md#detection).

## How it is tested

The same promises hold for every format only if they are tested for every format.

- **Reference oracles.** The native 7z reader is checked against `py7zr`, and the native
  RAR reader against `rarfile` and `unrar`: member metadata and decompressed bytes must
  match. The oracles are test dependencies only, never needed at run time.
  Opt-in runs compare against the test archives of `py7zr`, `rarfile` and libarchive.
- **One corpus, every format.** Each corpus entry describes an archive once and is built
  in every format it declares. Every build must open, list the expected members, read
  back the expected bytes and extract safely.
- **Hostile input.** Every corpus archive is also truncated, bit-flipped, zeroed and
  padded, and the read path must then either succeed or raise a typed
  [`ArchiveyError`][archivey.ArchiveyError], never another exception. A coverage-guided
  fuzzer runs nightly on the parsers, fixing up CRCs so it gets past the checksums.
  Property-based tests cover name normalization, the extraction safety checks and
  link resolution.
- **Platforms.** CI runs Linux on Python 3.11 to 3.14 with every extra, and with the
  zero-dependency core alone on the oldest and newest. macOS and Windows run on the
  oldest and newest Python. One leg pins each dependency to its oldest supported
  version, and one runs the free-threaded 3.13t build
  ([Platforms and threading](support-matrix.md)).

Depth: the [`testing-contract` spec](https://github.com/davitf/archivey/blob/main/openspec/specs/testing-contract/spec.md).
