# The source boundary hands over a wrapper, not the caller's stream

## Why

`archive-reading` says archivey never closes a caller-supplied `BinaryIO`, and the stream
layer keeps that by making every view and decoder borrow rather than own. Those defaults
govern the wrappers. They say nothing about the object underneath the first one, and a
caller's own stream reaching a backend unwrapped is one owning wrapper away from being
closed.

It was. Measured over the corpus, on `main`, with measurement enabled: opening ZIP,
`tar.gz`, `tar.bz2`, `tar.zst`, `tar.lz4`, `tar.lz`, `tar.zz` or `tar.br` from a `BytesIO`
or an `open()` handle closed that object when the reader closed. The chain is
`reader.close()` → the backend's teardown → `SeekCountingStream.close()`, and
`SeekCountingStream` is a `DelegatingStream`, which owns its inner — here the caller's own
object. Nothing in the layer was misconfigured; the caller's stream was simply inside it.

Measurement is the benchmark harness's switch, so no ordinary caller could reach the bug.
That is why nothing caught it, not a reason it was allowed: the same shape is one owning
wrapper away on every backend, and the rule it breaks is a published one.

`dev-docs/topics/stream-ownership.md` §2 claimed this could not happen — that `open_archive`
wraps a caller `BinaryIO` before any backend sees it, so "the owning default never reaches a
caller-supplied object". `open_archive` wraps only a **non-seekable** stream, and only in
`PeekableStream`, and only so detection's peeked prefix can be replayed. The doc carried
that sentence from the commit that created it; it described an intention, not the code.

## What changes

- The source boundary (`ensure_full_count_reads`) returns a wrapper for every stream source,
  never the caller's own object. The two sources that used to pass through — an already
  buffered seekable stream, and a non-seekable CPython buffer — get `BorrowedStream`.
- `BorrowedStream` is a `DelegatingStream` that forwards everything a pass-through can
  (reads, `readinto` zero-copy, seeks, `name`, `fileno`, the cheap size probe) and closes
  nothing.
- `DelegatingStream` gains `owns_inner`, defaulting to own. The default is what the
  layer's leak argument depends on and does not move; the flag only lets a class that owns
  nothing say so, in the vocabulary every other wrapper in the layer already uses.
- The property gets an end-to-end test over the corpus rather than a paragraph.

## Impact

- Affected specs: `access-mode-and-cost` (the source-boundary requirement).
- `archive-reading`'s no-close requirement is unchanged: the code comes up to meet it.
- A path source is untouched — nothing is wrapped, because the reader opens the handle
  itself and owns it.
- Behaviour a caller can observe: their stream is no longer closed. Everything else about
  the object handed to a backend is forwarded, and the corpus suite, the wrapper
  inventories and the leak oracle are unchanged by the wrapper's presence.
