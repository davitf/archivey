# Design — short-read-source-contract

No deeper design: the behaviour shipped in PR #219 and this change only records the
contract it already satisfies. Two decisions are worth keeping, because both were
argued during review and are easy to re-litigate wrongly.

## Decisions

**Buffer at the source boundary, not only in our parsers.** The failing `read(n)` is
frequently not archivey's: the ZIP failures came out of stdlib `zipfile._EndRecData64`,
TAR out of `tarfile.TarInfo.fromtarfile`, ISO out of `pycdlib`, and one out of the
`indexed_bzip2` accelerator. Fixing only archivey's parsers would have left ZIP, TAR and
ISO broken. The parser-level `read_exact` is still kept as defence in depth — those
parsers also read through `_HeaderDecryptStream` and `SlicingStream`, which no boundary
buffer sits in front of.

**Seekable sources only.** Buffering a non-seekable source would make `seekable()` report
the buffer's answer rather than the raw stream's, which `testing-contract` forbids
("MUST never implicitly buffer the non-seekable source to make it seekable").
Non-seekable sources need no help anyway: `PeekableStream._fill_to` already coalesces.

**Rejected: `read_exact` inside `SlicingStream`.** The first attempt made every sized
view read gather across shorts. That collapses the deliver-then-raise truncation shape
ADR-0014 requires — measured on a slice over a truncated xz/gzip/zlib decoder, the
recoverable prefix went from 201/247/248 bytes to zero, with `TruncatedError` pulled into
the first call. `SlicingStream` sits directly over decoder output in
`sevenzip_reader._open_member` and the `sevenzip_pipeline` LZMA `cap_size` stage, so this
was reachable. The view now coalesces with `read_full_count` (stop on the first short
non-empty return); the bounded `read(-1)` drain keeps `read_exact`, matching
`DecompressorStream.readall`'s complete-stream semantics. ADR-0014 anticipated exactly
this and prescribed the remedy used here: such an inner "needs a buffer in front".

**Test doubles must short `readinto` too.** `io.BufferedReader` over a `RawIOBase` that
implements only `read` raises `NotImplementedError`, so a double that omits `readinto`
cannot be buffered and would mis-report the fix as broken.
