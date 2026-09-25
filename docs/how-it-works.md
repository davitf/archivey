# How it works

How Archivey is built, for a reader deciding whether to trust it. Each section explains
one design choice and links to the material behind it. Nothing here changes how you call
the library; the pages it links to cover that.

## Native-first parsing

Archivey parses 7z and RAR headers itself, in Python, instead of wrapping `py7zr` or
`rarfile`. The first reason is cost. The project started as a tool to hash every member
of decades of old backups, and those libraries re-decompressed a solid block once per
member. The native 7z reader decodes each solid folder once per `stream_members()` pass.

The second reason is hostile input. A parser written in Python can be wrong, but it
cannot corrupt memory, and C archive parsers have a long history of memory-safety bugs
triggered by crafted files. The line is between parsing and decoding. Archive headers,
member tables, and the framing of xz and lzip streams are parsed in Python. Most
decompression runs in compiled code: stdlib `lzma`, `bz2` and `zlib` in the core, and
the `[recommended]` packages for PPMd, Deflate64, Brotli, Zstandard before Python 3.14,
and AES.

Depth: [ADR 0001](https://github.com/davitf/archivey/blob/main/dev-docs/decisions/0001-native-7z-not-py7zr.md)
(7z), [ADR 0002](https://github.com/davitf/archivey/blob/main/dev-docs/decisions/0002-native-rar-metadata-unrar-data.md)
(RAR).

## One stream layer for every codec

Format parsers do not call codec libraries directly. They build a chain of streams
(decrypt, decompress, verify) from one shared internal layer, and bytes are decoded only
as you read them. The 7z reader, the single-file compressors, compressed TAR, and ZIP's
unencrypted and AES members all decode through it; [`open_stream`][archivey.open_stream]
exposes it for a bare compressed file.

Because the layer is shared, the behaviour you rely on is written once. Codec exceptions
become [`CorruptionError`][archivey.CorruptionError] or
[`TruncatedError`][archivey.TruncatedError]. A stored CRC or hash is checked when a
member is read to its end, and the verdict comes from `read()`, never from `close()`. A
missing codec package raises an error that names the extra to install.

Depth: the [`compressed-streams` spec](https://github.com/davitf/archivey/blob/main/openspec/specs/compressed-streams/spec.md);
the [codec library analysis](https://github.com/davitf/archivey/blob/main/dev-docs/library-analysis.md)
for which library backs each codec, and why.

## Where the cost model comes from

Archive formats hide expensive operations. A backward seek in a compressed stream can
restart decompression from the beginning, and opening the members of a solid archive
out of order can decode the same block again for each one. Caches and guesses can hide
this, until a program that is fast on a ZIP turns quadratic on a `.tar.gz`. Archivey
reports the cost instead, and keeps the expensive capabilities off until you ask.

The report is a [`CostReceipt`][archivey.CostReceipt] on `reader.cost`, computed at open
before any member is read. It describes how listing works, whether members share a
solid block, and whether the source can seek. It never permits or refuses an access
pattern, and events at run time go to diagnostics rather than into the receipt.

The defaults are the other half. Member streams are forward-only and one is live at a
time until you pass `seekable_members=True` or `concurrent_members=True`, and random
access on a non-seekable source fails at open instead of buffering the input. A strict
default can be relaxed later without breaking callers; a permissive one cannot be
tightened without breaking them.

What to do with this: [Access costs and pitfalls](access-and-cost.md). Depth:
[ADR 0003](https://github.com/davitf/archivey/blob/main/dev-docs/decisions/0003-member-streams-opt-in.md),
[ADR 0010](https://github.com/davitf/archivey/blob/main/dev-docs/decisions/0010-no-silent-buffer-nonseekable.md).

## Backends and the registry

Every format backend registers when `archivey` is imported, including one whose
optional package is missing. Detection and backend selection are separate steps.
[`detect_format`][archivey.detect_format] peeks at the source without consuming it,
prefers magic bytes and content probes to the file extension, and records a
`FORMAT_EXTENSION_CONFLICT` diagnostic when the two disagree. For a compressed single
file it also decompresses the start and looks for a TAR header, so a `.gz` that holds a
tarball opens as `.tar.gz`. The registry then maps the detected format to its backend.

An extra never adds a backend; it adds the codec packages a backend uses.
[`format_availability`][archivey.format_availability] combines a backend with its codecs
and tools and reports [`FormatSupport`][archivey.FormatSupport] `FULL`, `PARTIAL` or
`NONE`. Without `[recommended]`, ISO is `NONE` and opening one raises
[`UnsupportedFormatError`][archivey.UnsupportedFormatError] with the install command. 7z
is `PARTIAL`: it lists and reads LZMA, LZMA2 and bzip2 members, and a PPMd member raises
[`PackageNotInstalledError`][archivey.PackageNotInstalledError] when you read it.

What to install: [Install and extras](install.md). The detection order and its evidence:
[Opening and listing](opening-and-listing.md#detection). Depth: the
[`backend-registry` spec](https://github.com/davitf/archivey/blob/main/openspec/specs/backend-registry/spec.md).

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
  ([Gotchas](gotchas.md#what-you-should-be-aware-of)).

Depth: [ADR 0006](https://github.com/davitf/archivey/blob/main/dev-docs/decisions/0006-stdlib-zipfile.md)
(ZIP). Per-format behaviour: [Formats and extras](formats.md).

## Decisions summary

The recorded choices that shape public behaviour, one line each. The full records are in
the repository's [decision log](https://github.com/davitf/archivey/tree/main/dev-docs/decisions).

| ADR | Outcome |
| --- | --- |
| 0001 | Native 7z reader; `py7zr` is a test oracle. BCJ2 folders are refused, not handed to another reader. |
| 0002 | Native RAR metadata, RARLAB `unrar` for compressed data; `rarfile` is a test oracle. |
| 0003 | Member streams are forward-only and one at a time unless you opt in. |
| 0004 | The access mode is `streaming: bool`: two real modes, not a three-value intent enum. |
| 0005 | The API is synchronous. From async code, use `asyncio.to_thread`. |
| 0006 | Stdlib `zipfile` for ZIP. Spanned sets it cannot address are refused. |
| 0007 | [`ArchiveMember`][archivey.ArchiveMember] is mutable so late fields fill in place. Treat it as read-only, use `replace()`, do not hash it. |
| 0008 | One accelerator library, `rapidgzip`, for gzip and bzip2 (`[seekable]`). |
| 0009 | Zstandard through `compression.zstd` or `backports.zstd`; `zstandard` short-read truncated frames. |
| 0010 | Random access on a non-seekable source fails at open. Nothing is buffered silently. |
| 0011 | The core install has no third-party runtime dependencies. |
| 0012 | [`ArchiveyUsageError`][archivey.ArchiveyUsageError] is not an `ArchiveyError`, so `except ArchiveyError` does not hide a caller bug. |
| 0013 | Name safety follows [`ExtractionPolicy`][archivey.ExtractionPolicy]: `STRICT` rewrites a name to a portable spelling where one exists, and refuses only what cannot be written safely. |
| 0014 | Integrity verdicts come from `read()`, never from `close()`. |
| 0015 | A zero-filled, block-aligned file is a valid empty TAR: reported with `EMPTY_ARCHIVE`, never refused. |
| 0017 | A bidi-override name is refused under `STRICT` and `STANDARD`; `TRUSTED` extracts it unchanged. |

ADRs 0016 and 0018 cover the test corpus and the review process.
