# Reading members

Getting bytes out of a member, and what each outcome means. Finding out what is
*in* the archive is [Opening and listing](opening-and-listing.md); what an access
pattern *costs* is [Access costs and pitfalls](access-and-cost.md).

## Read a member

```python
with archivey.open_archive("photos.zip") as reader:
    with reader.open("subdir/a.txt") as stream:
        data = stream.read()
```

By default streams are **forward-only** and only **one** may be live. Need seeking or
overlapping opens? Declare capabilities:

```python
with archivey.open_archive(
    "data.zip",
    seekable_members=True,
    concurrent_members=True,
) as reader:
    ...
```

**Truncated or CRC-mismatched streams:** `data = stream.read()` asks for the whole stream and
raises (`TruncatedError` / `CorruptionError`) — it does not return a partial body. That is
intentional: a silent lossy success is worse than not salvaging. To recover a truncated
prefix, use a chunked loop (`read(n)` until empty or exception). Content faults raise from
`read`, never from `close`. When a member is short *and* carries a digest, the error type is
best-effort (`TruncatedError`); shortfall and digest mismatch are not always separable —
`except archivey.ReadError` catches both — see
[Errors and diagnostics](errors-and-diagnostics.md) for the full tree, including why
mistakes in *your* code are deliberately kept out of it.

## What a read gives you back

Reading a member to its end verifies it where the archive stores a checksum, and
raises rather than quietly handing you short or wrong data. Two things are worth
knowing here; the full contract is on
[Errors and diagnostics](errors-and-diagnostics.md#the-integrity-guarantee).

- **`read(member.size)` behaves differently for the two failures.** On corruption it
  raises and withholds the chunk that reached the size. On truncation it returns a
  **short buffer with no exception** — known-wrong bytes are held back, an apparently
  incomplete prefix is handed over. So a short return from that call is a signal:
  check the length.
- **The ordinary chunked loop is safe.** It delivers every readable byte and *then*
  raises, so it cannot end quietly on a damaged member:

```python
buf = bytearray()
try:
    with reader.open("member.bin") as stream:
        while chunk := stream.read(1 << 20):
            buf.extend(chunk)
except archivey.ReadError:
    ...  # buf holds everything that was readable; the member is damaged
```

## Streaming mode (pipes)

```python
with archivey.open_archive(sys.stdin.buffer, streaming=True) as reader:
    for member, stream in reader.stream_members():
        ...  # single forward pass
```

In streaming mode, `members()` / `get()` / `open()` / `read()` raise
`UnsupportedOperationError`. Use `__iter__`, `stream_members`, or `extract_all` once.

## One-shot extract

`archivey.extract(src, dest)` extracts everything with safe defaults — see
[Safe extraction](extracting.md).

There is deliberately no `members=` on the one-shot helper: selecting a subset needs
the member list, which would force open / list / reopen. Use an already-open reader
instead (`reader.extract_all(members=...)`).

Note that `extract()` **accepts a non-seekable source** and opens it in streaming
mode, because extraction is a single forward pass — while `open_archive` refuses one
without `streaming=True`.
