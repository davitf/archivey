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

## Native-first parsing

Archivey parses 7z and RAR headers itself, in Python, instead of wrapping `py7zr` or
`rarfile`. Neither library fit a pull-based stream model. When the project started,
`py7zr` decompressed a solid block again for each member read from it. Its current API
replaced that `read()` with a push model, where you pass a factory and the library writes
decompressed data into the objects it makes. Turning that back into a stream you read
from took more code than parsing the headers.

The other reason is consistency. Archivey turns every format's member names and
metadata into one model by the same rules. Both libraries apply their own rules first,
which Archivey would have had to copy, and sometimes undo.

Depth: [7z](https://github.com/davitf/archivey/blob/main/dev-docs/formats/7z.md) and
[RAR](https://github.com/davitf/archivey/blob/main/dev-docs/formats/rar.md) handbook pages.

## One stream layer for every codec

Most codecs are shared between formats: LZMA sits inside 7z, `.xz` and `.tar.xz`, and
Deflate inside ZIP, 7z and gzip. So there is one internal stream layer. It deals with
each codec library's quirks, gives each codec the best support it can, including
incremental reads and seeking where the codec allows it, and every format builds on it.
The 7z reader, the single-file compressors, compressed TAR, and ZIP's unencrypted and
AES members all decode through it; [`open_stream`][archivey.open_stream] exposes it for a
bare compressed file.

Because the layer is shared, the behaviour you rely on is written once. Codec exceptions
become [`CorruptionError`][archivey.CorruptionError] or
[`TruncatedError`][archivey.TruncatedError]. A stored CRC or hash is checked when a
member is read to its end, and the verdict comes from `read()`, never from `close()`. A
missing codec package raises an error that names the extra to install.

The layer also draws the line between Python and compiled code. Archive headers, member
tables, and the framing of xz and lzip streams are parsed in Python, which can be wrong
but cannot corrupt memory; C archive parsers have a long history of memory-safety bugs
set off by crafted files. Most decompression runs in compiled code: stdlib `lzma`, `bz2`
and `zlib` in the core, and the `[recommended]` packages for PPMd, Deflate64, Brotli and
Zstandard before Python 3.14. `.Z` is the exception, decoded in Python as well. AES
decryption uses the `cryptography` package.

Depth: the [codec library analysis](https://github.com/davitf/archivey/blob/main/dev-docs/library-analysis.md)
for which library backs each codec, and why.

## Where the cost model comes from

Archive formats hide expensive operations, and caches and guesses can hide them further,
until a program that is fast on a ZIP turns quadratic on a `.tar.gz`. Archivey reports
the cost instead.

The report is a [`CostReceipt`][archivey.CostReceipt] on `reader.cost`, computed at open
before any member is read. It describes how listing works, whether members share a
solid block, and whether the source can seek. It never permits or refuses an access
pattern, and events at run time go to diagnostics rather than into the receipt.

What to do with it: [Access costs and pitfalls](access-and-cost.md).

## Backends and the registry

Every format backend registers when `archivey` is imported, including one whose
optional package is missing. Detection and backend selection are separate steps.
[`detect_format`][archivey.detect_format] peeks at the source without consuming it,
prefers magic bytes and content probes to the file extension, and records a
`FORMAT_EXTENSION_CONFLICT` diagnostic when the two disagree. For a compressed single
file it also decompresses the start and looks for a TAR header, so a `.gz` that holds a
tarball opens as `.tar.gz`. The registry then maps the detected format to its backend.

An extra supplies one of two things: the library a backend cannot work without, or the
codec packages a container's members may use.
[`format_availability`][archivey.format_availability] combines the two and reports
[`FormatSupport`][archivey.FormatSupport] `FULL`, `PARTIAL` or `NONE`. ISO needs
`pycdlib`, so without `[recommended]` it is `NONE` and opening one raises
[`UnsupportedFormatError`][archivey.UnsupportedFormatError] with the install command. 7z
only lacks some member codecs, so it is `PARTIAL`: it opens and lists, members in the
stdlib-backed codecs read (see [Formats and extras](formats.md#7z)), and a PPMd member
raises [`PackageNotInstalledError`][archivey.PackageNotInstalledError] when you read it.

What to install: [Install and extras](install.md). The detection order and its evidence:
[Opening and listing](opening-and-listing.md#detection).

## What is not ours

- **ZIP: stdlib `zipfile`** reads the central directory. Member data is sliced from the
  file and decoded by the shared stream layer, except ZipCrypto members, which
  `zipfile` decrypts. The stdlib keeps ZIP in the zero-dependency core; the alternatives
  bring native dependencies.
- **TAR: stdlib `tarfile`** parses the headers, reading decompressed bytes from the
  stream layer for `.tar.gz` and the other compressed forms. `tarfile` can stop at a
  corrupt header and return a short listing, so Archivey checks the end of the archive
  after a scan ([TAR](formats.md#tar-and-compressed-tar)).
- **RAR data: RARLAB `unrar` or `rar`.** RAR compression is proprietary, and a native
  decompressor is out of scope. Archivey runs the binary as a separate process, passes
  the password on stdin, and reads the decoded bytes from its output, with one process
  for a whole solid `stream_members()` pass. Uncompressed members that are not solid,
  encrypted or split are read without it. `unrar-free` and `unar` are never fallbacks.
- **ISO 9660: `pycdlib`**, a pure-Python library in `[recommended]`. Archivey installs a
  directory-cycle guard inside it at import
  ([ISO 9660](formats.md#iso-9660)).

Depth: the [ZIP handbook page](https://github.com/davitf/archivey/blob/main/dev-docs/formats/zip.md).
Per-format behaviour: [Formats and extras](formats.md).

## How it is tested

The same promises hold for every format only if they are tested for every format.

- **Reference oracles.** The native 7z and RAR readers are checked against `py7zr` and
  the `7z` command, and against `rarfile` and `unrar`: member metadata and decompressed
  bytes must match. The oracles are test dependencies only, never needed at run time.
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
- **Platforms.** CI runs Linux on Python 3.11 to 3.14, both with every extra and with
  the zero-dependency core alone, plus macOS and Windows, and a leg pinned to the oldest
  supported version of each dependency.

Depth: the [`testing-contract` spec](https://github.com/davitf/archivey/blob/main/openspec/specs/testing-contract/spec.md).
