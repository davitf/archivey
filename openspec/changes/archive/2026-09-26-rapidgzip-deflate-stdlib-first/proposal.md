# rapidgzip reads DEFLATE only after the stdlib engine proves the input complete

## Why

rapidgzip 0.16 aborts the Python process (`std::terminate` on a `std::logic_error`, "The
bit buffer should not contain more data than have been read from the file!") when it
decodes a gzip, zlib or raw DEFLATE stream that ends early. The throw comes from a
destructor in its chunk decoder, so it happens for a path source, a Python file object
and an in-memory buffer alike. `_TrappingSource` cannot contain it, and no `try/except`
can. Measured on an 8 MB gzip of base64 text: 27 of 30 random cuts of 1 to 200 000 bytes
aborted, and a 380 KB file aborts too. `open_archive(path, seekable_members=True)` on a
truncated `.gz` of 1 MiB or more killed the interpreter; the default open raised
`TruncatedError`.

Only a full decode shows that a DEFLATE stream is complete, so rapidgzip must not see a
stream that has not been decoded to a clean end.

A second defect: `_translate_rapidgzip` maps a fixed list of messages, and rapidgzip
reports most C++ faults as `RuntimeError` with an internal message. One ("Next block
offset index is out of sync!") reached the caller untranslated.

## What Changes

- The gzip, zlib and raw-deflate codecs, when they select rapidgzip, decode with the
  stdlib engine first. A backward seek switches the stream to rapidgzip if the stdlib
  engine has decoded the whole input to a clean end: during the caller's reads, at a
  seek to the end, or in a separate pass that the seek runs. If that pass fails, the
  stream stays on the stdlib engine, and the caller meets the failure on a read.
- A failed pass keeps an `ArchiveyError` it saw and raises it again at the clean end of
  the stdlib stream, for a source stage (ZIP WinZip AES) that reports its verdict once.
- Every rapidgzip `RuntimeError` translates (to `CorruptionError` unless a listed message
  says truncation), for gzip, zlib, deflate and bzip2. An exception that the caller's
  own source raised, and that the trap re-raised, is not translated.
- bzip2 is unchanged: no truncation or bit flip aborted `IndexedBzip2File` in 110
  mutated 8 MB inputs.
- Truncation of a standalone zlib or raw-deflate stream on the accelerator path is now
  detected; the "may short-read" limitation is gone.

## Impact

- Code: `internal/streams/codecs.py`.
- Performance: a first sequential pass over a seekable gzip/zlib/deflate stream runs at
  stdlib speed instead of rapidgzip's; the first backward seek on a stream not yet read
  to its end costs one extra stdlib pass. Numbers in `dev-docs/known-issues.md`.
- Benchmarks: `zip_read_all_accel_on` no longer engages rapidgzip, so the harness's
  "ON seeks more than OFF" engagement check is removed.
- Tests: `tests/test_accelerator_truncation_abort.py` (crash cases in a child
  interpreter), updates to `test_rapidgzip_deflate_zlib.py`, `test_zip_aes.py`,
  `test_benchmark_gate.py` and the stream inventories.
