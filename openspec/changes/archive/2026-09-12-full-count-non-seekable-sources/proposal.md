## Why

`ensure_full_count_reads` is the source boundary that makes `read(n)` return the full
count short of EOF, because a raw `read(n)` may legally return short and header parsers —
archivey's and the stdlib's alike — read a short return as EOF. It applies that to
seekable sources and returns non-seekable ones **unchanged**.

Nothing inside `streamtools` then supplies the guarantee. Three unrelated mechanisms
outside it happen to cover the three live paths:

| Path | What actually coalesces today |
| --- | --- |
| `open_archive`, detection runs (`format=None`) | `PeekableStream` — wrapped at `core.py:371` *inside* the `if resolved_format is None:` branch, so it is absent whenever the caller passes `format=` |
| `open_stream`, any `format=` | `PeekableStream` — wrapped at `core.py:592` **unconditionally** for a non-seekable source, with no `ensure_full_count_reads` in that branch at all |
| `format=` explicit, compressed | `ensure_bufferedio` inside `DecompressorStream.__init__` |
| `format=` explicit, plain TAR, non-seekable | **stdlib `tarfile._Stream.__read`** (traced to `tarfile.py:565`) |

The last row is the defect. `streamtools` is dependency-free stdlib plumbing, documented
as liftable into a standalone library; a healthy archive read from a short-returning pipe
is protected there by a CPython implementation detail it does not own and cannot rely on.

The two entry points also differ, which matters for where the wrapper has to go:
`open_archive` routes non-seekable stream sources through `resolve_source` →
`ensure_full_count_reads` and wraps in `PeekableStream` only to detect, while `open_stream`
never calls `resolve_source` and wraps every non-seekable source in `PeekableStream`
regardless of `format=`. So today `PeekableStream` is the *only* coalescing layer on the
`open_stream` non-seekable path.

**No live failure.** A `.tar.gz` and a plain `.tar` over a non-seekable `max_chunk=1`
source both open and read back correctly, with and without `format=`. What ships is a
boundary that does not honour its own contract, covered by luck.

The spec already records the gap in its own title: `testing-contract` has *"Short-returning
source coverage for **seekable** sources"*. The non-seekable half was never written, and no
test double crosses the two axes — `ShortReadBytesIO` is seekable-only, `NonSeekableBytesIO`
delegates to `BytesIO` and is therefore always full-count. That is why nothing catches this.

## What Changes

- **`FullCountStream` in `streamtools`** — supplies the guarantee with **zero read-ahead of
  its own**. It is a single read plus a `read_exact` fallback behind a stream interface: each
  pass asks the inner for exactly the bytes still missing, so a `read(n)` on it takes exactly
  `n` bytes from the source, holds no buffered bytes, and reports `seekable()` as `False`.
  Codec layers above the boundary may still buffer; this change does not alter that, and
  `design.md` D2 records why that is safe where it happens and not here.
  - It stays **transparent** to the duck-typed metadata probes (`name`, and `size` /
    `try_get_size` via `peel_for_source_size`), so `ResolvedSource.archive_name` and
    `compressed_source_size` do not degrade. A FIFO opened with `open()` is non-seekable and
    *does* carry `.name`, and the seekable branch already preserves it, so this keeps the two
    branches consistent rather than adding a courtesy. `tell()` still raises — the
    seek-required refusals depend on it — and `fileno` is not forwarded.
- **`ensure_full_count_reads` wraps non-seekable sources in it** instead of returning them
  unchanged, and becomes **idempotent**: handed a `FullCountStream` it returns it unchanged.
  The seekable branch keeps `BufferedReader` (already idempotent), whose read-ahead is
  recoverable by seeking and which also collapses the header parsers' many tiny reads.
- **`PeekableStream` owns its own precondition and its `_fill_to` loop goes.** The loop exists
  to gather a full count from a short-returning inner, so it is redundant once the inner is
  full-count — but only if the inner actually is, and at `core.py:592` it is not. So
  `PeekableStream.__init__` applies `ensure_full_count_reads` to its own inner (free, given
  idempotence) and `_fill_to` collapses to a single `read`. No call site has to remember,
  and `core.py` needs no edit. The class stays — `peek` is a job the full-count wrapper does
  not do.
- **`testing-contract` grows the non-seekable half** of short-returning coverage, and a
  `ShortReadNonSeekable` double that is short-returning *and* non-seekable.

This does **not** relax ADR 0010 (`no-silent-buffer-nonseekable`) or
`testing-contract`'s "MUST never implicitly buffer the non-seekable source to make it
seekable". Both forbid making a pipe seekable and materializing it into memory or a temp
file. `FullCountStream` does neither: no buffered bytes, `seekable()` stays `False`, and
random access over a non-seekable source still fails fast at open.

## Impact

- **Capabilities:** `access-mode-and-cost`, `testing-contract`.
- **Code:** `internal/streams/streamtools/base.py` (new `FullCountStream` — it subclasses
  `ReadOnlyIOStream`, and `binaryio.py` cannot import that without a circular import),
  `internal/streams/streamtools/binaryio.py` (`ensure_full_count_reads`: wrap the
  non-seekable branch, become idempotent), `internal/streams/peekable.py` (wrap own inner,
  simplify `_fill_to`), `internal/streams/streamtools/slice.py` (two "needs a buffer in
  front" comments), `tests/streams_util.py` (parameterize the short-read double).
  **Not `core.py`** — see `design.md` D5.
- **Public API:** unchanged. No requirement is relaxed; one is widened and one is renamed.
