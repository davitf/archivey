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
copies of FILE headers stored after the members, with a pointer from MAIN. Listing
reads QO first, seeks back to after MAIN, and skips FILE headers already in QO.
Default WinRAR AUTO may omit small files from QO; those still list from their
local headers. `-qo+` copies every header, so that skip is one seek to QO.
Unreadable QO falls back to walking every FILE header. With no usable QO there is no central
directory: each header states its own size, so the parser walks header-to-header,
seeks past every member's packed data, and open-time cost scales with member
count. Once open, `members()` / `get()` return from the in-memory table at O(1)
cost.

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

With `seekable_members=True`, every member stream from random `open()` reports
`seekable() is True` and `seek()` works. A stream from `stream_members()` never seeks,
with or without the flag, on every format: the pass owns the position, and a seek would
decode again behind it. To seek inside a member, open it with `open()`. How the backend
does it varies:

- XZ / lzip can seek via native indexes
- gzip / zlib / raw deflate / bzip2 can use `[seekable]` (`rapidgzip`) when installed
- RAR compressed members seek by respawning `unrar`. On a solid archive that
  re-decodes from the start, including members before the one you opened
- otherwise a backward seek may **re-decompress from the start**

Encrypted members seek like any other when `seekable_members=True`; the cost is the
codec's. ZipCrypto restarts decryption from the member's start on a backward seek.
WinZip AES and encrypted 7z restart at the target's cipher block. A seek that moves
the position gives up a CRC check until a seek back to 0, but not WinZip AES's HMAC:
the HMAC covers the ciphertext, so the read that reaches the member's end first reads,
without decrypting, whatever ciphertext your seeks skipped, and then checks it.

A seek that lands before the start of a member behaves like `io.BytesIO`: a relative
seek (`SEEK_CUR` or `SEEK_END`) clamps to position 0, and a negative `SEEK_SET` offset
or an unknown `whence` raises `ValueError`. A directory member is a real file, so a
relative seek before its start reaches the OS and raises `OSError`. A seek past the end
of a TAR member returns the member size rather than the position you asked for, where
other formats return the target; reads from there return `b""` either way. An offset or
`whence` that is not an integer (`seek(1.5)`, `seek(None)`) raises `TypeError`, as on
`io.BytesIO`, and leaves the position where it was. A `read` size that is neither an
integer nor `None` (`read(1.5)`) raises `TypeError` the same way, and an integer too
large for the platform's index type (`read(2**70)`) raises `OverflowError`, both before
anything is read.

Whether a seek that moves the position gets a diagnostic is decided by **what the seek
actually costs**, not by the codec's name: `STREAM_REWIND_REDECOMPRESSES` fires when the
rewind discards more than about a megabyte of decoded progress — the bytes you would
have to decode again to get back where you were. On a solid RAR that includes the prefix
in front of this member, not just the bytes already read from this stream. That matters
because a format that *can* carry an index does not always *have* a useful one. A
single-block `.xz` (what `lzma.compress` and un-threaded `xz` produce) has exactly one
seek point, at the origin, so rewinding it costs the same as rewinding a codec with no
index at all — and an engaged `rapidgzip` can hold an index sparse enough for the same
thing. Small rewinds stay quiet on every codec unless the carrier declares a higher
floor (solid RAR's prefix).

If you set a `DiagnosticPolicy` to `RAISE` on that code as a guard against accidentally
quadratic seek loops, note that it fires on **every** qualifying seek, not only the
first — the report still records one entry.

The flag changes what member streams can *do*, and nothing else. It does not change what
`members()` reports: the xz index and lzip trailer are read from any seekable source, so
`member.size` and `member.hashes` are the same with and without it.

Under `ArchiveyConfig.use_rapidgzip=AUTO` (the default), rapidgzip is selected only when
seekability is declared **and** the known compressed input is at least
`RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` (16 MiB). rapidgzip runs in a child process that
takes about 45 ms to start and open, and it saves about 3.4 ms per MB of compressed input
over the stdlib, so it is only faster from about 13 MB. Smaller members stay on stdlib
`zlib`/`gzip`. Set
`use_rapidgzip=ON` to force the accelerator regardless of size, or `OFF` to disable it.
`ON` needs a source that can seek: on a pipe, or on a member stream of an outer archive
opened without `seekable_members=True`, it raises `StreamNotSeekableError`. For a zlib
stream (`.zz`, `.tar.zz`), `ON` also checks the Adler-32, which rapidgzip does not, and
that check needs every byte from the start: a seek forward moves the skipped bytes out of
the child process. Listing a `.tar.zz` under `ON` therefore transfers the whole
decompressed archive once, even when no member is read, and a single far seek transfers
everything before it. `AUTO` never gives a bare zlib stream to rapidgzip.

The two settings differ when `rapidgzip` is not installed. `ON` is a request, so it
raises `PackageNotInstalledError` naming `[seekable]` — even without
`seekable_members=True`, at the first gzip, zlib or deflate stream it would handle.
`AUTO` treats the accelerator as an enhancement and falls back to the stdlib decoder
without raising. The stream is still seekable, but a backward seek may re-decode from
the start. `use_indexed_bzip2` behaves the same way for bzip2.

An accelerator raises on the same corrupt input as the stdlib decoder, with one kind of
exception: crafted stream boundaries that the data's own checksums cannot see. Inside a
ZIP member, a second compressed stream or bytes after the first stream's end end the
member on the stdlib path, while the accelerator reads on; the member's declared size and
CRC then decide. In a multi-member `.gz`, rapidgzip does not check the length field
(ISIZE) of a member before the last, but it does check every member's CRC.

Declare seek only when you need it (e.g. parquet-in-zip random reads).

## Concurrent member streams

Default: at most one live member stream. A second overlapping `open()` raises
`ArchiveyUsageError` (a usage error — not an `ArchiveyError`).

```python
open_archive(src, concurrent_members=True)
```

After members are materialized, workers may `open()` different members concurrently.
Same-stream access still needs caller synchronization. Reader-wide passes
(`__iter__` / `stream_members` / `extract_all`) remain single-owner.
`streaming=True` cannot combine with `concurrent_members=True`.

## Non-seekable sources

`streaming=False` (default) **fails fast** if the format needs seek and the source is a
pipe. Archivey will not silently buffer a **non-seekable** source into memory or a temp
file to fake seekability. Use `streaming=True` for pipes and sockets — it works for TAR
(including compressed tar) and the single-file compressors.

ZIP, ISO, 7z and RAR keep their index at the end of the archive or address it by
offset, so they need seek in **either** mode; `streaming=True` cannot open them from a
pipe. The error says so directly rather than proposing a retry that would be refused,
and the fix is to buffer the source to a file or a `BytesIO` first.

A seekable stream is not that pipe case. RAR still needs a filesystem path for compressed
member data (RARLAB `unrar` or `rar`, or `unar`), so a `BytesIO` or file object may be copied to a
temp file when a compressed member is read. `archive.cost.notes` states that caveat at
open, with the limit that bounds it; when the limit already rules the copy out, the note
says such a read will be refused instead. Path sources do not copy, with one exception
the notes also state: a RAR with a prefix before it (an SFX stub, say) read with
`rar_decompressor="unar"`, which does not look past a prefix, is copied from where the
RAR starts, within the same limit. `rar_decompressor="unrar"` reads it in place. (A
list of RAR volume files is linked into a temp directory when the files are not side by
side, and copied within the limit only where the system allows no link.)

The copy is of the whole archive (every volume, for a volume set), it happens on the
first member read that goes through `unrar` rather than at open, and it is removed when
the reader closes. Listing never needs it. `ArchiveyConfig.spool_limits` bounds it:
`SpoolLimits.max_bytes` defaults to 1 GiB, counted across a volume set. An archive over
the limit raises `ResourceLimitError` before anything is written, and later reads on that
reader are refused the same way. `None` (`SpoolLimits.UNLIMITED`) removes the limit, and
`0` refuses every copy, which leaves only the members archivey reads without `unrar`
(stored members of a non-solid archive). The copy goes to the platform temporary
directory; where that is memory-backed (`tmpfs`), the limit bounds memory rather than
disk. The same limit holds what a solid pass keeps for RAR5 file copies (`rar -oi`):
the pass keeps each copy's source as it decodes it, so the copy does not decode the
solid stream again. Up to 8 MiB per pass stays in memory; the rest goes to a temporary
file that counts against the limit from the source's first decoded byte until the pass
ends, after any copy of the archive the pass needs. A source the limit has no room for
is decoded again instead of being refused. Extraction keeps nothing for a source it
writes: it copies each copy from the file it just wrote. If you only need the copies'
digests, `stream_members(file_copy_streams=False)` yields `None` for each copy and keeps
nothing; the copy's digests are its source's (`link_target_member`).

## Streaming mode is one pass

With `streaming=True`, the first of `__iter__` / `stream_members` / `extract_all`
consumes the pass. A second call raises — including after an early `break`. Use
`scan_members()` to finish/drain when you need a full list after a partial pass.

## Passwords and confirmation cost

Multiple password candidates can trigger confirmation reads. ZipCrypto **STORED** members
are the expensive niche: a wrong candidate that passes the weak open check may force a
full-member CRC scan. So is a **PPMd** member under either ZIP encryption: its decoder is
not relied on to reject a wrong key. A WinZip AES member that is stored, PPMd or small is
read once to its HMAC per candidate that passes the two-byte check, before the caller's
read starts.
Encrypted **7z folders** have no check value at all, so the first read into a folder
confirms the password by decoding. That decode stops at the first
member CRC covering at least 4 bytes, so a solid folder's first small member settles it.
For a compressed folder it also stops at 64 KiB of output: the codec rejects a wrong key
within a few bytes, so a wrong candidate costs the key derivation and little else. The
one expensive case left is **store/copy+AES** (or PPMd) with its only CRC at the end of
a large folder: nothing rejects a wrong key before that CRC, so each candidate reads to
it. Prefer a single known password there.

A password that only a weak check accepted, or that no check tested at all (RAR3/4
encrypted data has none), leaves the member's own checksum as the real test, and that
runs at EOF. Closing such a stream part way through emits
`ENCRYPTED_MEMBER_UNVERIFIED`: the bytes you read may have decrypted with a wrong
password. Read to EOF, or pass the one password you know, when that matters.

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

rapidgzip 0.16 also aborts the process on a gzip, zlib or raw deflate stream that ends
early, whatever the source. So archivey runs those three decoders in a **child process**:
the abort ends the child, and your read raises `TruncatedError` (or `CorruptionError`
where the abort does not say why). Starting the child costs about 45 ms per accelerated
stream; under `AUTO` that is paid only for streams of 16 MiB compressed or more. Where no
child can start (a frozen application, archivey imported from a zip, or a spawn or
temporary file the OS refuses), `AUTO` reads these codecs with the standard library and
`ON` raises `ResourceLimitError`. The `AUTO` fallback logs one warning per process on the
`archivey.streams` logger, naming the reason: it is a fact about the environment, not
about your archive. Set `use_rapidgzip=OFF` to never start a child, and to silence that
warning.

rapidgzip decodes ahead in parallel, so it can reach the cut and abort before your
reads get there. When the child aborts, the standard library takes over from the last
point rapidgzip's index gave for what you have read, so you get the same bytes and the
same `TruncatedError` as with `use_rapidgzip=OFF`. The standard library decodes again
only from that index point to where you had read: a few MiB on most files, and a few
percent of a very large one, never the whole file.

bzip2 runs in your process. It did not abort on cut or damaged input in the tests behind
this page (cuts, bit flips, CRC damage, as path and as file object), but that is testing,
not a guarantee: an input that aborts the bzip2 decoder would end your process. Details:
[known issues](https://github.com/davitf/archivey/blob/main/dev-docs/known-issues.md).

## Checklist

| Situation | Prefer |
| --- | --- |
| Hash / process every member | `stream_members()` or `__iter__` |
| Solid archive, many named opens | Reorder to archive order, or one streaming pass |
| Need `seek()` on a member | `seekable_members=True` (+ `[seekable]` for gz/bz2/zlib/deflate) |
| Thread pool of member readers | `concurrent_members=True` after `members()` |
| stdin / socket | `streaming=True` for TAR and the single-file compressors; buffer ZIP / ISO / 7z / RAR to a file or `BytesIO` first ([above](#non-seekable-sources)) |
| “Just unzip it safely” | `open_archive(src)`, then `reader.extract_all(dest)` |
