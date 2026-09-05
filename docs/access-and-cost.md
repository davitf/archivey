# Access costs and pitfalls

Archivey’s defaults keep the common path cheap and fail loudly when you ask for something
expensive. This page is the “how not to shoot yourself in the foot” guide.

## Wall-time bands (aspirational)

These are **targets**, not CI hard-fails. Absolute ratios vary by host; the PR gate
enforces structural invariants (bytes decompressed, seeks, solid decode-once) instead.
The change-guarded nightly publishes full wall ratios — re-run locally with:

```bash
uv run --extra all python -m benchmarks.harness --mode full --scale realistic
```

Recorded measurements, with their host and commit, live in
[`benchmarks/RESULTS.md`](https://github.com/davitf/archivey/blob/main/benchmarks/RESULTS.md)
rather than here — they go stale faster than this page is revised.

| Workload | Aspirational band |
| --- | --- |
| Large-member ZIP/TAR/gzip **read** (decompression-dominated) | ≤ **1.3×** stdlib peer |
| ZIP/TAR **extract** (safety floor) | ≤ ~**2×** stdlib peer |
| ZIP/TAR **open+list** (wraps stdlib) | ≤ **2–3×** `zipfile` / `tarfile` |
| 7z/RAR **open+list** (native parsers) | ≈parity (~**1.25×**) vs `py7zr` / `rarfile` |

These are the targets, not a claim about your machine. Measured ratios are
host-dependent enough that publishing one number here would be misleading: the
codec-dominated rows are stable, but the ZIP wrapper rows move by a third or more
between runners. Run the command above to get figures for your own hardware.

Everyday listing and extract are fine for most callers at the ratios we see. The
residual ZIP listing gap is mostly per-member derivation cost; **lazy
`ArchiveMember` derivation (L5)** is the named follow-up, deferred past the first
public release (see `IDEAS.md`).

## Read `reader.cost`

Every open archive exposes a machine-readable receipt:

| Field | Meaning |
| --- | --- |
| `listing_cost` | `INDEXED` / `REQUIRES_SCANNING` / `REQUIRES_DECOMPRESSION` |
| `access_cost` | `DIRECT` (member N independent) or `SOLID` (may need earlier bytes) |
| `stream_capability` | `SEEKABLE` source vs `FORWARD_ONLY` |
| `solid_block_count` | Distinct solid blocks, when known |

Cost never changes what is *legal* — it describes what your access pattern will *pay*.

`StreamCapability` is ordered — `FORWARD_ONLY < SEEKABLE` — because a seekable source
can serve every read a forward-only one can. That is what lets you compare a source
against a format's stated minimum (`format_availability(fmt).required_source`) instead
of trying the open and catching the failure; see
[Opening and listing](opening-and-listing.md). `listing_cost` and
`access_cost` are *not* ordered: their values name kinds of work, not strengths.

### RAR listing cost

RAR reports `listing_cost=INDEXED`: the native parser builds the member table at
open, before `members()` is called. RAR5 can carry a **Quick Open** record (`QO`) —
a stored copy of the file headers at the tail, located from MAIN. When that record
is present, stored, and unencrypted, listing reads it (same table extract uses).
Otherwise there is no central directory: each header states its own size, so the
parser walks header-to-header, seeks past every member's packed data, and open-time
cost scales with member count. Once open, `members()` / `get()` return from the
in-memory table at O(1) cost.

## Solid archives: prefer one forward pass

On solid 7z / RAR (and compressed TAR, which is solid for random member access), opening
members out of order can **re-decode the same block** for each `open()`.

**Do this:**

```python
for member, stream in reader.stream_members():
    consume(stream)   # one decode of each solid block
```

**Avoid this on solid archives** (unless you accept the cost):

```python
for name in wanted_names:
    with reader.open(name) as s:   # may restart the solid block each time
        ...
```

`AccessCost.SOLID` and `solid_block_count` tell you when this matters.
`concurrent_members=True` does **not** remove solid open-order cost — it only makes
overlapping streams correct.

## Seeking inside compressed members

Without `seekable_members=True`, member streams report `seekable() is False` and
`seek()` raises `io.UnsupportedOperation`. That is intentional: seek indexes and
accelerators are not built until you ask.

With `seekable_members=True`:

- XZ / lzip can seek via native indexes
- gzip / zlib / raw deflate / bzip2 can use `[seekable]` (`rapidgzip`) when installed
- RAR compressed members seek by respawning `unrar`. On a solid archive that
  re-decodes from the start, including members before the one you opened
- otherwise a backward seek may **re-decompress from the start**

Whether that gets a diagnostic is decided by **what the seek actually costs**, not by the
codec's name: `STREAM_REWIND_REDECOMPRESSES` fires when the rewind discards more than
about a megabyte of decoded progress — the bytes you would have to decode again to get
back where you were. On a solid RAR that includes the prefix in front of this member,
not just the bytes already read from this stream. That matters because a format that
*can* carry an index does not always *have* a useful one. A single-block `.xz` (what
`lzma.compress` and un-threaded `xz` produce) has exactly one seek point, at the origin,
so rewinding it costs the same as rewinding a codec with no index at all — and an
engaged `rapidgzip` can hold an index sparse enough for the same thing. Small rewinds
stay quiet on every codec unless the carrier declares a higher floor (solid RAR's
prefix).

If you set a `DiagnosticPolicy` to `RAISE` on that code as a guard against accidentally
quadratic seek loops, note that it fires on **every** qualifying seek, not only the
first — the report still records one entry.

The flag changes what member streams can *do*, and nothing else. It does not change what
`members()` reports: the xz index and lzip trailer are read from any seekable source, so
`member.size` and `member.hashes` are the same with and without it.

Under `ArchiveyConfig.use_rapidgzip=AUTO` (the default), rapidgzip is selected only when
seekability is declared **and** the known compressed input is at least
`RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` (1 MiB). Smaller members stay on stdlib `zlib`/`gzip`
so archives of many tiny entries do not pay per-stream accelerator setup. Set
`use_rapidgzip=ON` to force the accelerator regardless of size, or `OFF` to disable it.

Declare seek only when you need it (e.g. parquet-in-zip random reads).

## Concurrent member streams

Default: at most one live member stream. A second overlapping `open()` raises
`ConcurrentAccessError` (a usage error — not an `ArchiveyError`).

```python
open_archive(src, concurrent_members=True)
```

After members are materialized, workers may `open()` different members concurrently.
Same-stream access still needs caller synchronization. Reader-wide passes
(`__iter__` / `stream_members` / `extract_all`) remain single-owner.
`streaming=True` cannot combine with `concurrent_members=True`.

## Non-seekable sources

`streaming=False` (default) **fails fast** if the format needs seek and the source is a
pipe. Archivey will not silently buffer the whole archive into memory or a temp file.
Use `streaming=True` for pipes and sockets — it works for TAR (including compressed
tar) and the single-file compressors.

ZIP, ISO, 7z and RAR keep their index at the end of the archive or address it by
offset, so they need seek in **either** mode; `streaming=True` cannot open them from a
pipe. The error says so directly rather than proposing a retry that would be refused,
and the fix is to buffer the source to a file or a `BytesIO` first.

## Streaming mode is one pass

With `streaming=True`, the first of `__iter__` / `stream_members` / `extract_all`
consumes the pass. A second call raises — including after an early `break`. Use
`scan_members()` to finish/drain when you need a full list after a partial pass.

## Passwords and confirmation cost

Multiple password candidates can trigger confirmation reads. ZipCrypto **STORED** members
are the expensive niche: a wrong candidate that passes the weak open check may force a
full-member CRC scan. Prefer a single known password when reading huge stored encrypted
members.

## Accelerators and source lifetime

The `[seekable]` path uses `rapidgzip` (gzip / zlib / raw deflate + bzip2), which is
C++ and does not tolerate its Python source disappearing mid-decode: upstream, that
raises through a `terminate()` boundary and aborts the process.

**Archivey contains that.** A caller-owned source is wrapped so the fault becomes a
benign EOF toward the accelerator and is re-raised as an ordinary Python exception —
verified in `tests/test_accelerator_bug3_trap.py`, which asserts the untrapped path
aborts while archivey's exits cleanly. So closing a source underneath a live stream is
a clean failure, not a crash. Still don't do it: the stream is dead and the read
fails.

One residual is genuinely upstream and not contained: some **path**-source truncations
and CRC mismatches can still `std::terminate` during worker finalization after a Python
exception. Details:
[known issues](https://github.com/davitf/archivey/blob/main/dev-docs/known-issues.md).

## Checklist

| Situation | Prefer |
| --- | --- |
| Hash / process every member | `stream_members()` or `__iter__` |
| Solid archive, many named opens | Reorder to archive order, or one streaming pass |
| Need `seek()` on a member | `seekable_members=True` (+ `[seekable]` for gz/bz2/zlib/deflate) |
| Thread pool of member readers | `concurrent_members=True` after `members()` |
| stdin / socket | `streaming=True` for TAR and the single-file compressors; buffer ZIP / ISO / 7z / RAR to a file or `BytesIO` first ([above](#non-seekable-sources)) |
| “Just unzip it safely” | `archivey.extract(src, dest)` |
