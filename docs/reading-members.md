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
`except archivey.ReadError` catches both.

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
[Safe extraction](safe-extraction.md).

There is deliberately no `members=` on the one-shot helper: selecting a subset needs
the member list, which would force open / list / reopen. Use an already-open reader
instead (`reader.extract_all(members=...)`).

Note that `extract()` **accepts a non-seekable source** and opens it in streaming
mode, because extraction is a single forward pass — while `open_archive` refuses one
without `streaming=True`.
