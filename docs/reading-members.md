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

## The integrity guarantee

**Read a member to its end and Archivey checks it.** Where the archive stores a
checksum or an authentication tag, a full read verifies it and raises if it does not
match. Stop early and nothing is checked. Errors always come from `read()`, never from
`close()` — so a `finally` block can't mask one.

"To its end" means `read(-1)`, reading until `read()` returns `b""`, or — for a member
with a declared size — reading that many bytes.

What that does and does not promise:

- **We try to raise on every error we can detect** — not on every error. Some formats
  store no checksum at all, and some damage decodes into something that looks
  perfectly valid.
- **`CorruptionError` vs `TruncatedError` is a best-effort guess, not a diagnosis.**
  Damage that happens to decode into a shorter stream is indistinguishable from a
  genuine truncation. Don't branch on which one you got — `except archivey.ReadError`
  catches both.
- **Bytes delivered before the error are of unknown quality.** When a compressed
  member fails mid-stream, some of what you already read is probably fine — but we
  can't tell you which part, or how much. Treat the prefix as unverified: not
  known-good, not known-bad.
- **A full-length return means the checksum matched.** Trust it as far as you trust
  that digest.
- **A short return with no exception does not mean "complete".** `read(member.size)`
  on a truncated member hands back what it has and stays quiet. Check the length — or
  just read again, because the *next* read raises.

That last point is what makes the ordinary chunked loop safe: it delivers every byte
that was readable and *then* raises, rather than ending quietly on a short member. So
the recoverable prefix and the error both reach you.

```python
buf = bytearray()
try:
    with reader.open("member.bin") as stream:
        while chunk := stream.read(1 << 20):
            buf.extend(chunk)
except archivey.ReadError:
    ...  # buf holds everything that was readable; the member is damaged
```

If you need certainty regardless of how you read — partial reads, seeks, or "never
hand me unverified bytes" — `VerificationMode.STRICT` verifies a whole member before
returning any of it.

### What each call does

For a member whose declared size is 500 bytes, truncated after 110:

| Call | Corrupt at full length | Truncated after 110 of 500 |
| --- | --- | --- |
| `read(109)` | not yet at the end — no error | returns 109, no error |
| `read(110)` | not yet at the end — no error | returns 110, no error |
| `read(111)` | not yet at the end — no error | returns 110; the next `read()` raises |
| `read(member.size)` | raises `CorruptionError` | returns 110 short, **no exception** |
| `read(-1)` | raises `CorruptionError` | raises `TruncatedError` |
| chunked until `b""` | raises on the chunk that reaches the size, and withholds it | delivers the prefix, then raises |
| partial read, then `close()` | quiet — you stopped early | quiet — you stopped early |

The one row worth remembering is **`read(member.size)`**: it raises on corruption but
returns a short buffer on truncation. Known-wrong bytes are withheld; an apparently
incomplete prefix is handed over. So a short return from that call is a signal, not a
success — check the length.

Members with no declared size have nothing to read *to*, so `read(n)` can't
self-certify at all. Use `read(-1)` or read until `b""`.

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
