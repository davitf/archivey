# Zero bytes between gzip members or bzip2 streams end the data

## Why

A bare `.gz` or `.bz2` file holding a member or stream, zero bytes, then another one
read as both payloads, with the accelerator off and on. With the accelerator on, the
bzip2 path got there by a second decode of the file from the start, because rapidgzip's
bzip2 decoder stops at the zeros. The official tools stop there: `bzip2` 1.0.8 writes the
first payload and warns "trailing garbage after EOF ignored"; GNU `gzip -dc` writes the
first member and warns "decompression OK, trailing garbage ignored". 7-Zip, `pbzip2`,
`lbzip2`, `bsdcat`, `unar`, Python's `bz2.decompress` and rapidgzip stop there too. Python's
`gzip` is the one reader measured that reads on. No known writer puts zero bytes between
members or streams. The open gap in `dev-docs/design-rules.md` asked whether archivey
should stop there; the maintainer ruled that it should (2026-10-10).

## What changes

For gzip and bzip2, zero bytes after a member or stream are padding only when they run
to the end of the file. After them, the first non-zero byte is trailing data, also when it
starts another member or stream: `ARCHIVE_TRAILING_DATA` at that byte under the default
policy, `DiagnosticRaisedError` under strict. The same holds with the accelerator off,
`AUTO` and `ON`. Members or streams with nothing between them still read as one payload,
and empty bzip2 streams right after a stream are still part of the data.

The report names the first non-zero byte, as every other trailing-data report does after
zero padding.

The bzip2 accelerator's end check no longer hands a stream after zeros to the standard
library; it reports it. Its layout walk accepts only empty streams between two streams.
The handover stays for a stream header right after the data, where the decoder leaves a
cut or damaged stream alone and the standard library raises.

Alternatives not taken:

- **Keep reading past the zeros** (the behaviour this replaces). It matches Python's
  `gzip` only, costs the accelerated bzip2 path a second decode, and has no producer.
- **Report the first zero byte.** Every other trailing-data report names the first
  non-zero byte after zero padding.

zstd, LZ4 and LZMA Alone keep reading past zero bytes between streams; xz keeps its
format-defined stream padding. Neither was part of the ruling.

## Impact

- `format-single-file-compressors`: the trailing-data requirement says where zeros are
  padding for gzip and bzip2, with two scenario rows.
- `seekable-decompressor-streams`: the bzip2 and gzip accelerator rows.
- Code: `framed_decoder.py` (`padding_ends_data`), `bzip2_codec.py` (end scan, layout
  walk), `deflate_decoder.py` (`GzipDecoder`).
