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

## The integrity guarantee

> **Read a member to its end: a corrupt member raises `CorruptionError` on a `read()`;
> a truncated member raises `TruncatedError` or returns short of its declared size; a
> clean member returns all its bytes, checksum-verified. Stop before the end and it is
> not verified. `close()` never raises a content error (target contract; best-effort
> today on a few backends).**

"To its end" means `read(-1)` / `readall`, reading until `read()` returns `b""`, or —
for a member with a **declared** size — reading that many bytes. For that whole-member
read, each outcome tells you what the library claims — with the honesty caveats below:

- a **`CorruptionError`** means we have positive evidence of wrongness — **discard
  everything read from this member; none of it is trustworthy** as a complete intact
  member (the raising call returns nothing). A digest / auth mismatch is the clear
  case; mid-stream structural failures are likewise treated as untrustworthy;
- a **`TruncatedError`** means the member appears **incomplete** — the bytes already
  returned are a **best-effort salvageable prefix**, not a proven-correct prefix
  (corruption that decodes to a shorter stream is easily labeled truncation). The
  raising call itself returns nothing; do not treat the prefix as the whole member;
- a **full-length** return (`len == member.size`, or a subsequent `b""`) means the
  content was **checksum-verified** — trust it under the digest's strength;
- a **short** return (`len < member.size`, no exception) means **truncation-shaped** —
  an apparent incomplete member; **"no exception" does not mean "complete."** Check
  the length, or read again to get the `TruncatedError`. Prefix correctness remains
  best-effort.

Corruption that the library can *prove* (digest / auth mismatch, over-run) is caught
whenever such a read reaches the end — independent of whether `close()` is ever
called. Callers who must verify **regardless** of access pattern (partial reads,
seeks, or "never release unverified bytes") use `VerificationMode.STRICT`
(`verification-integrity-mode`), which fully verifies a member before returning any of
it.

### Call × failure matrix (size-declared member)

Assume a member truncated after 110 decompressed bytes with `member.size == 500`:

| Call | Corrupt at full length | Truncated after 110 of 500 |
| --- | --- | --- |
| `read(109)` (from start) | (n/a — not yet at end) | returns 109, **no error** (did not ask past available) |
| `read(110)` (from start) | (n/a — not yet at end) | returns 110, **no error** (exactly available) |
| `read(111)` (from start) | (n/a) | returns short 110; following `read()` raises `TruncatedError` |
| `read(member.size)` | raises `CorruptionError` | returns short (`len < size`), **no exception** |
| `read(-1)` / `readall` | raises `CorruptionError` | raises `TruncatedError` |
| chunked until `b""` | raises on the read that reaches the size (withholds that chunk) | delivers the whole prefix; first read *past* available returns short; the next raises `TruncatedError` |
| partial read, then `close()` | quiet (early stop) | quiet (early stop) |

The load-bearing asymmetry: **`read(member.size)` raises on corruption but returns a
short buffer on truncation** — because a known digest failure yields wrong bytes
(withheld) while a truncation-shaped end yields an apparent incomplete prefix
(delivered). This is a **deliberate idiom** ("return the available prefix; raise on
known-wrong bytes"), not a trap. Size-unknown members have no `member.size` to read
to, so a bare `read(n)` cannot self-certify at all — use `read(-1)` / read-to-`b""`.

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
