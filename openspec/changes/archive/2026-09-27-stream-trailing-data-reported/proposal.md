# Single-file codecs: read past appended bytes and report them

## Why

A compressed file with bytes after its stream (a signature appended to a download,
padding from a tool) was handled differently per codec: gzip, zstd, LZ4 and Brotli
raised; zlib, bzip2, xz, LZMA Alone and lzip ignored the bytes without a word, and xz
and lzip lost their size and seeks, because the index is found from the end of the file. The reference tools differ
too: `xz`, `lzma` and `zstd` refuse such a file, `bzip2` warns and succeeds. The
payload is intact in every case, so refusing it helps no one, but ignoring the bytes
silently hides a file that is not what it claims.

## What changes

- `format-single-file-compressors`: every codec except `.Z` delivers the whole
  payload, then reports non-zero bytes after the end as `ARCHIVE_TRAILING_DATA`.
  Zeros are padding, as for TAR. Another stream of the same codec is still read as
  more data. xz and lzip find their index within 1 MiB of trailing bytes. Brotli tells
  trailing bytes from damage by decoding again, so a pipe keeps `CorruptionError`.
- `format-single-file-compressors`: zero bytes read as an empty LZMA Alone stream, since
  13 zero bytes are a valid empty header and the rest is padding.
- `diagnostics`: `ARCHIVE_TRAILING_DATA` takes `expected_marker="end_of_stream"`, with
  the codec name as `format`.

The report comes from a bare compressed file, a compressed TAR and `open_stream`
only. Inside ZIP and 7z the container's sizes decide, so the codec stops silently.

## Impact

Code: `internal/streams/decompressor_stream.py`, `decompress.py`, `codecs.py`,
`xz.py`, `lzip.py`, `internal/config.py`, `diagnostics.py`, the single-file and TAR
readers, `core.py`. Docs: `docs/formats.md`, `docs/gotchas.md`, the codec pages under
`dev-docs/formats/`. Tests: `tests/test_stream_trailing_data.py` and updates to the
codec, single-file and seekable-stream tests.
