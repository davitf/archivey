# Codec-stream diagnostics reach the caller's collector

## Why

The xz, lzip and unix-compress decompressors were built inside `Codec.open` without a
diagnostic collector, so their `SEEK_INDEX_DEGRADED` reports went to a throwaway one:
`DiagnosticPolicy.strict()` did not raise, `on_diagnostic` never fired, and
`reader.diagnostics` did not count them. Once they reach the caller's collector, a
`RAISE` disposition can fire from inside a decode or an index scan, where the stream
cannot unwind.

## What Changes

- `StreamConfig` carries the collector into every `Codec.open`; the single-file reader
  passes its collector like the other readers.
- An escalated report is raised once the stream is consistent, so a caller that catches
  it keeps a working handle, including the member verifier behind it.
- A stream's seek table thinned more than once records one report, and `RAISE` raises
  on every thinning.

## Impact

- `seekable-decompressor-streams` spec: the seek-index degradation requirement and matrix.
- `archivey.internal.config`, `streams/codecs.py`, `streams/decompressor_stream.py`,
  `streams/archive_stream.py`, `diagnostics_collector.py`, `backends/single_file_reader.py`.
