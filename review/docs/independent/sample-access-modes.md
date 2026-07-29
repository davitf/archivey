# Sample page — Access modes, streams, and cost

*Probe page: depth and voice. This is the other page without which everyday use
fails in ways that look like library bugs.*

---

Opening an archive is not one operation. You are declaring **how** you will walk
it, **what** member streams are allowed to do, and paying the format’s **cost
receipt** whether you read it or not.

## Random access vs streaming

```python
# Default: random access — needs a seekable source (file, BytesIO, …)
with archivey.open_archive("a.zip") as ar:
    print(ar.get("README.md"))
    with ar.open("README.md") as f:
        data = f.read()

# Pipe / socket: say so up front
with archivey.open_archive(sys.stdin.buffer, streaming=True) as ar:
    for member, stream in ar.stream_members():
        ...
```

`streaming=False` (default) **fails fast** on a non-seekable source. Archivey
will not silently buffer a pipe into memory. Formats whose index lives at the
end (ZIP, ISO, 7z, …) cannot be read from a pipe even with `streaming=True` —
buffer to disk or `BytesIO` first.

One exception people trip on: **`archivey.extract()`** on a pipe auto-selects
streaming mode, because extraction is already a single forward pass. Mixing
mental models (“extract worked, open_archive didn’t”) is expected unless this is
documented.

On a streaming reader, `members()`, `get()`, `open()`, and `read()` raise
`UnsupportedOperationError`. Use `stream_members()`, finish with
`scan_members()`, or peek an index with `members_report_if_available()` (never
consumes the pass).

## Member stream capabilities

By default a reader allows **one live member stream**, **forward-only**:

```python
s1 = ar.open("a.txt")
ar.open("b.txt")  # ConcurrentAccessError — points at your open_archive() call site
```

Opt in explicitly:

```python
from archivey import MemberStreams

with archivey.open_archive(
    path,
    member_streams=MemberStreams.CONCURRENT | MemberStreams.SEEKABLE,
) as ar:
    ...
```

- **`CONCURRENT`** — overlapping `open()` calls are allowed. Reader-wide passes
  (`stream_members`, `extract_all`, iteration) stay single-owner. You still
  synchronize any stream objects you share across threads.
- **`SEEKABLE`** — `seek()` works where the backend can position. Without it,
  `seek` raises `io.UnsupportedOperation` even on a `BytesIO`-backed ZIP.

`streaming=True` combined with `CONCURRENT` is rejected: one progressive decoder
cannot fan out.

`extract()` / `extract_all()` do not require these flags — they drive their own
single pass.

## The cost receipt (read this before fanning out)

```python
with archivey.open_archive(path) as ar:
    print(ar.cost)
    # CostReceipt(listing_cost=..., access_cost=..., stream_capability=..., ...)
```

Three **orthogonal** axes:

| Axis | Asks | Examples |
|---|---|---|
| `listing_cost` | How hard is enumerating names? | ZIP/7z/RAR/ISO: `INDEXED`; bare tar / directory: `REQUIRES_SCANNING`; `.tar.gz`: `REQUIRES_DECOMPRESSION` |
| `access_cost` | Can I open member N alone? | ZIP: `DIRECT`; solid 7z / compressed tar: `SOLID` |
| `stream_capability` | Can the *source* rewind? | File: `SEEKABLE`; pipe: `FORWARD_ONLY` |

**`CONCURRENT` does not cancel `SOLID`.** Opening solid members out of order
fails or forces expensive re-decompression. For solid archives, prefer
`stream_members()` in archive order, or plan using `solid_block_count` when
present.

Compressed tar is the common surprise: the file is seekable on disk, yet
`access_cost` is `SOLID` and listing needs decompression. A directory tree is
seekable and direct to read, but listing is a scan — not an index.

## Which listing API?

| Need | Call |
|---|---|
| Complete list or raise | `members()` |
| Damaged archive, keep a prefix | `members_report()` and check `error` |
| Streaming / finish the pass | `scan_members()` |
| Cheap peek if already indexed | `members_report_if_available()` |
| Bounded memory, archive order | `stream_members(...)` |

`stream_members` is also the escape hatch past `ListingLimits`: it will iterate
members that would trip `max_members` on `members()`. Extraction prep still
enforces the limits configured **at open** — passing a looser `config=` into
`extract_all` will not raise them.

## Stream lifetime

```python
# Wrong — streams die when the iterator advances
streams = [s for _, s in ar.stream_members() if s]
streams[0].read()  # may fail

# Right — read before advancing
for member, stream in ar.stream_members():
    if stream is None:
        continue
    with stream:
        process(stream.read())
```

Yielded streams open **lazily**. Skipping a member costs nothing; a wrong
password surfaces when you first read, not when you iterate past the name.

`read(member)` loads the entire member into memory with no size cap — prefer
`open` for anything not known to be small.

## Mid-file archives

If you hand archivey a seekable stream positioned at offset K, the archive is
assumed to **start at K**. You do not need to slice it yourself; do not forget
to seek there.

## Practical decision tree

1. Untrusted extract everything → `extract()` (see Safe extraction).
2. Need random `get`/`open` → default `open_archive` on a file.
3. stdin / pipe → `streaming=True` + `stream_members` / `extract`; not ZIP-from-pipe.
4. Thread pool over members → `MemberStreams.CONCURRENT`, and check `cost.access_cost`
   is `DIRECT` first.
5. Need `seek` on member streams → `MemberStreams.SEEKABLE`; for gzip-family
   speed, install `[seekable]` and understand AUTO’s 1 MiB threshold.
6. Huge member tables → avoid `members()`; stream or raise `ListingLimits`.
