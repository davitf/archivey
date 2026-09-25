# Tasks — codec-stream diagnostics

## 1. Code

- [x] 1.1 `StreamConfig.collector`, filled by `open_codec_stream`, passed to the xz, lzip
      and unix-compress streams; the single-file reader passes its collector.
- [x] 1.2 `DiagnosticCollector.deferring_raises()`; `DecompressorStream` raises held
      reports once consistent; `ArchiveStream.seek` updates its verifier when a seek raises.
- [x] 1.3 Every thinning of one stream's table escalates under `RAISE`.

## 2. Tests

- [x] 2.1 xz and lzip under `open_archive`: counted, called back, raised under `strict()`,
      and readable after the caught raise.
- [x] 2.2 Raises mid-read, from the xz per-stream scan, and from a seek under a member
      verifier leave the right bytes readable; repeated thinning raises each time.

## 3. Archive

- [x] 3.1 `openspec archive codec-stream-diagnostics --yes`
