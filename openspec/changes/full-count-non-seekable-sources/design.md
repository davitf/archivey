# Design — full-count reads for non-seekable sources

## Context

`ensure_full_count_reads` (`streamtools/binaryio.py`) is the one boundary every archive
source crosses. Its job is narrow: turn the `io.RawIOBase` *up-to-n* `read(n)` contract
into a full-count one, so a header parser that pulls a fixed-size structure with a single
`read(n)` cannot mistake a legal short return for EOF. That failure mode is not
hypothetical — it is what reported healthy archives as corrupt and produced the archived
`short-read-source-contract` change.

The function opts out for non-seekable sources. The opt-out predates the measurement
work and its stated justification has been wrong in every revision of the docstring so
far. See §Decisions.

## Decisions

### D1 — A wrapper, not an error

Refusing a short-returning non-seekable source is not available: specs require
non-seekable streaming. `format-single-file-compressors` §Requires seek says every
supported single-file codec "SHALL stream from non-seekable sources", and
`testing-contract`'s non-seekable matrix requires `.tar.gz` through `FakeNonSeekable` to
iterate and read. A source cannot be refused for exercising a contract `io.RawIOBase`
explicitly permits and the specs explicitly require.

### D2 — The wrapper needs no buffer, and that is the whole point

"Returns the full count" and "consumes no more than asked" look like they trade off.
They only do so for `io.BufferedReader`, which reads *ahead*: ask it for 20 bytes and it
pulls 8 KiB from the raw. On a seekable source that over-read is recoverable; on a pipe
it is not, and a `_NonClosingBufferedReader` that later detaches strands whatever it
pulled.

Gathering the count without read-ahead is already implemented in this module as
`read_exact`: each pass asks for exactly `n - len(gathered)`, so the total consumed is
exactly `n` (or everything up to EOF). The wrapper is that function behind a stream:

```python
class FullCountStream(ReadOnlyIOStream):
    """Make a short-returning inner full-count without reading ahead."""
    def read(self, n: int = -1, /) -> bytes:
        if n < 0:
            return drain-to-EOF
        return read_exact(self._inner, n)
```

Zero buffered bytes, nothing to strand on close, `seekable()` inherited `False` from
`ReadOnlyIOStream`. This reframes the seekable branch too: `BufferedReader` is correct
there *because* read-ahead is recoverable by seeking and because it collapses the
parsers' tiny reads (the archived measurement: a 1000-member RAR listing went from 2007
reads to 7). Neither benefit applies to a pipe; neither is needed there.

### D3 — `read_exact`, not `read_full_count`

`slice.py` documents three gather policies (ADR 0014) and the difference is what a short
return *means*. At the source boundary the inner is a raw byte source, not a decoder with
a deferred truncation error to preserve, so a short means "ask again" — `read_exact`.
That matches what `BufferedReader` gives the seekable branch, which keeps the two halves
of `ensure_full_count_reads` behaviourally identical. `read_full_count` would stop on the
first short and reintroduce the exact bug this boundary exists to prevent.

### D4 — This is not the buffering ADR 0010 forbids

ADR 0010 (`no-silent-buffer-nonseekable`) and `testing-contract`'s "MUST never implicitly
buffer the non-seekable source **to make it seekable**" are one rule about two things:
don't fake seekability, and don't materialize a pipe into memory or a temp file. ADR
0010's Context is explicit — *"buffers a pipe into memory or a temp file to 'make ZIP
work'"*.

`FullCountStream` holds zero bytes, reports `seekable()` as `False`, and changes nothing
about random access: `streaming=False` over a non-seekable source still fails fast at
open. Conflating "don't buffer a non-seekable source" with "don't fix short reads on a
non-seekable source" is the error that kept producing wrong docstrings; the two are
independent and only one is a rule.

### D5 — What this does and does not simplify in `PeekableStream`

`PeekableStream` does two jobs: pushback for `peek` (detection needs it — the peeked
prefix is replayed to the backend), and full-count gathering in `_fill_to`. Only the
second is redundant once the inner is full-count:

```python
# before: loops because the underlying stream may return short
while len(self._buffer) < n:
    chunk = self._underlying.read(n - len(self._buffer))
    if not chunk: break
    self._buffer.extend(chunk)
```

Over a full-count inner that is one `read`. Roughly four lines. The class is **not**
deletable and the wrapper is not a replacement for it — this is about one class owning
the guarantee instead of three mechanisms covering three paths.

Composition order is `PeekableStream(FullCountStream(raw))`: the peek buffer sits above
the guarantee, so replayed prefix bytes and pass-through reads are both full-count.

### D6 — Cost

One Python-level loop per read on the non-seekable path, where nothing stood before. For
a well-behaved source the loop body runs once (the first `read` returns the full count)
and exits. No allocation beyond the returned bytes when a single read satisfies the ask.
Nothing on the seekable path changes, so the `VISION.md` budget paths (path and seekable
stream sources) are untouched.

## Rejected alternatives

| Alternative | Why not |
| --- | --- |
| Raise `UnsupportedFeatureError` for short-returning non-seekable sources | Contradicts specs that require non-seekable streaming (D1); also undetectable at wrap time — shortness is only observable mid-read |
| Wrap non-seekable sources in `BufferedReader` too | Unrecoverable over-consumption from a pipe, and `_NonClosingBufferedReader.close()` detaches, stranding the read-ahead (D2) |
| Leave it and document the gap | What ships today. The guarantee then depends on `tarfile._Stream` internals for one live path, in a package documented as dependency-free and liftable |
| Fold the guarantee into `PeekableStream` and always wrap non-seekable sources in that | Ties a detection concern to a source-boundary one, puts the boundary's guarantee in `streams/` above `streamtools/`, and pays a pushback buffer on paths that never peek |

## Open question for implementation

`ConcatenatedFile` (multi-volume) normalizes items individually through
`_coerce_path_or_stream` → `ensure_full_count_reads`, and its own `read` is documented as
coalescing across volumes. Confirm whether a non-seekable volume item needs the wrapper
per item, or whether `ConcatenatedFile`'s own gather already covers it — and if the
latter, whether that gather is `read_exact`- or `read_full_count`-shaped (D3).
