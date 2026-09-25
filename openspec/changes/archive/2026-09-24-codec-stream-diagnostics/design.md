# Design — codec-stream diagnostics

The raise is deferred rather than unwound at each emit site. The xz per-stream scan
emits from inside `XzDecoder.feed` before the decoder advances its stream cursors, so a
`finally` at the emit site cannot save the bytes that feed already decoded.
`DiagnosticCollector.deferring_raises()` holds, per thread, what an emit or
`escalate_only` would raise; `DecompressorStream` opens it around `read`, `readall`,
`seek` and its size query and raises once its state is consistent. Nested blocks share
the outermost one, so stacked streams raise once, at the outer stream.

The listing-time size probe in the single-file reader keeps the throwaway collector: it
answers `size=None` when the index is unreadable, and reporting there would count one
file twice and refuse the open under `strict()` for a caller who never seeks.
