## Why

`ensure_full_count_reads` is the source boundary that makes `read(n)` return the full
count short of EOF, because a raw `read(n)` may legally return short and header parsers —
archivey's and the stdlib's alike — read a short return as EOF. It applies that to
seekable sources and returns non-seekable ones **unchanged**.

Nothing inside `streamtools` then supplies the guarantee. Three unrelated mechanisms
outside it happen to cover the three live paths:

| Path | What actually coalesces today |
| --- | --- |
| Detection runs (`format=None`) | `PeekableStream` — wrapped in `core.py` *inside* the `if resolved_format is None:` branch, so it is absent whenever the caller passes `format=` |
| `format=` explicit, compressed | `ensure_bufferedio` inside `DecompressorStream.__init__` |
| `format=` explicit, plain TAR, non-seekable | **stdlib `tarfile._Stream.__read`** (traced to `tarfile.py:565`) |

The third row is the defect. `streamtools` is dependency-free stdlib plumbing, documented
as liftable into a standalone library; a healthy archive read from a short-returning pipe
is protected there by a CPython implementation detail it does not own and cannot rely on.

**No live failure.** A `.tar.gz` and a plain `.tar` over a non-seekable `max_chunk=1`
source both open and read back correctly, with and without `format=`. What ships is a
boundary that does not honour its own contract, covered by luck.

The spec already records the gap in its own title: `testing-contract` has *"Short-returning
source coverage for **seekable** sources"*. The non-seekable half was never written, and no
test double crosses the two axes — `ShortReadBytesIO` is seekable-only, `NonSeekableBytesIO`
delegates to `BytesIO` and is therefore always full-count. That is why nothing catches this.

## What Changes

- **`FullCountStream` in `streamtools`** — supplies the guarantee with **zero read-ahead**.
  It is `read_exact` behind a stream interface: each pass asks the inner for exactly the
  bytes still missing, so it never consumes more than the caller asked for, holds no
  buffered bytes, and reports `seekable()` as `False`.
- **`ensure_full_count_reads` wraps non-seekable sources in it** instead of returning them
  unchanged. The seekable branch keeps `BufferedReader`: its read-ahead is recoverable by
  seeking, and it also collapses the header parsers' many tiny reads.
- **`PeekableStream` becomes pure pushback.** Its `_fill_to` loop exists to gather a full
  count from a short-returning inner; over a full-count inner it collapses to a single
  `read`. The class stays — `peek` is a job the full-count wrapper does not do.
- **`testing-contract` grows the non-seekable half** of short-returning coverage, and a
  `ShortReadNonSeekable` double that is short-returning *and* non-seekable.

This does **not** relax ADR 0010 (`no-silent-buffer-nonseekable`) or
`testing-contract`'s "MUST never implicitly buffer the non-seekable source to make it
seekable". Both forbid making a pipe seekable and materializing it into memory or a temp
file. `FullCountStream` does neither: no buffered bytes, `seekable()` stays `False`, and
random access over a non-seekable source still fails fast at open.

## Impact

- **Capabilities:** `access-mode-and-cost`, `testing-contract`.
- **Code:** `internal/streams/streamtools/binaryio.py` (new class, one branch),
  `internal/streams/peekable.py` (simplify `_fill_to`), `tests/streams_util.py` (new double).
- **Public API:** unchanged. No requirement is relaxed; one is widened and one is renamed.
