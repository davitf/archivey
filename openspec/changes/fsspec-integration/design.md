# Design: fsspec integration

## Context

Two things are asked for, and they share one mechanism:

1. **Remote archives in few requests.** A caller has an archive on HTTP, S3 or GCS and
   wants to list it or read one member without downloading it. This is
   [scenario 9](../../../dev-docs/scenarios.md#9-loading-from-a-remote-file-object), and
   [DR-10a](../../../dev-docs/design-rules.md#dr-10a-read-sequentially-seek-only-when-the-format-needs-it)
   states the rule: read forward in few large reads, read only the index to list and only
   the member's bytes to read it.
2. **Archives as fsspec file systems.** A pandas, dask or pyarrow user writes
   `pd.read_csv("archivey://data.csv::s3://bucket/set.7z")` and gets archivey's formats,
   limits and checks behind fsspec's interface.

The second needs the first: an `archivey::s3://` URL opens a remote archive.

Constraints that bind the design:

- **ADR 0011, zero-dependency core.** fsspec is never a dependency of `archivey`.
- **ADR 0005, sync-only API.** fsspec runs its own event loop for async back ends; the
  archivey side stays synchronous.
- **ADR 0010, no silent buffering of a non-seekable source.** A remote file is seekable
  through range requests, so nothing here buffers a whole source. The RAR spool, which
  already exists and is bounded by `SpoolLimits`, is the one whole-archive copy.
- **Maintainer, 2026-10-09:** "Archivey does not need to accept a URL itself". A `str`
  passed to `open_archive()` stays a local path.
- **DR-9a, the upload-server test.** Read-ahead memory is a fixed bound that does not
  grow with the archive.

## Measurements

Taken on `main` at 8808382, 2026-10-21. Corpus: 300 files, 42 MB, every tenth file
random bytes and the rest text, archived eight ways. Each run opens the archive, lists
it, and for "one" also reads `tree/dir05/file004.txt` (148 KB, near the middle). A local
HTTP/1.1 server with `Range` support logs every request. The scripts and raw logs are
kept beside the project's earlier HTTP range-reading report (2026-09-29), which used
the same method on an older `main`.

Fetch policies:

- **naive**: one GET for exactly each read archivey makes. This shows archivey's own
  read pattern.
- **fsspec default**: `HTTPFileSystem().open(url)`, which is the `bytes` cache with 5 MiB
  blocks. This is what a caller gets today without choosing anything.
- **fsspec blockcache 256 KiB**: the better of fsspec's caches here.
- **doubling**: a read-ahead that doubles on each sequential read up to 8 MiB and resets
  on a jump (the `adaptive` policy of the September study).

GETs and bytes fetched; the HEAD that learns the size is left out of every column.

| Archive (size) | Op | naive | fsspec default | blockcache 256 KiB | doubling |
| --- | --- | --- | --- | --- | --- |
| ZIP deflate (26.5 MB) | list | 6, 37 KB | 4, 5.3 MB | 2, 332 KB | 3, 135 KB |
| | one | 9, 94 KB | 5, 10.5 MB | 3, 594 KB | 4, 266 KB |
| 7z non-solid (25.3 MB) | list | 3, 12 KB | 3, 5.3 MB | 2, 417 KB | 2, 89 KB |
| | one | 5, 70 KB | 4, 10.5 MB | 4, 941 KB | 3, 220 KB |
| 7z solid (24.1 MB) | list | 3, 12 KB | 3, 5.3 MB | 2, 492 KB | 2, 99 KB |
| | one | 52, 12.6 MB | 8, 31.5 MB | 50, 13.1 MB | 10, 19.0 MB |
| RAR5 (26.1 MB) | list | 11, 78 KB | 7, 25.0 MB | 7, 1.8 MB | 7, 518 KB |
| | one | 36, 26.2 MB | 13, 55.3 MB | 104, 27.1 MB | 26, 27.3 MB |
| RAR5 solid (24.8 MB) | list | 16, 118 KB | 8, 33.5 MB | 12, 3.1 MB | 12, 773 KB |
| | one | 40, 24.9 MB | 14, 62.5 MB | 103, 26.8 MB | 34, 25.1 MB |
| TAR (43.0 MB) | list | 297, 2.4 MB | 15, 68.2 MB | 132, 34.4 MB | 156, 28.1 MB |
| | one | 299, 2.6 MB | 16, 73.6 MB | 134, 34.9 MB | 157, 28.3 MB |
| tar.gz (26.0 MB) | list | 400, 27.0 MB | 8, 36.4 MB | 101, 26.0 MB | 12, 26.0 MB |
| | one | 417, 28.1 MB | 9, 41.7 MB | 106, 27.3 MB | 17, 28.0 MB |
| tar.xz (24.3 MB) | list | 373, 24.3 MB | 8, 34.7 MB | 94, 24.4 MB | 11, 24.3 MB |
| | one | 388, 25.3 MB | 9, 40.0 MB | 98, 25.5 MB | 15, 25.3 MB |

What the table says:

- **No single cache policy is right.** The fsspec default fetches 5 MiB per miss, so
  listing any archive costs at least 5 MB, and the RAR header walk and the TAR header
  hops fetch more than the whole archive. Small blocks fix the bytes and multiply the
  requests on sequential data. Doubling is best for sequential data and for listing, and
  still wrong for TAR, where every hop over member data resets it.
- **archivey's own read pattern is already close to the minimum in bytes** for ZIP and
  non-solid 7z (naive column). The native ZIP reader halved the bytes since September
  (37 KB to list, against 102 KB). What remains is request count: 6 GETs where 2 would do.
- **The waste is in the gap between archivey and the cache.** The cache cannot know
  that a ZIP open needs the last 64 KiB and nothing else, or that a TAR hop of 50 KB is
  cheaper to read through than to jump. The backend knows both.
- **RAR reading downloads the archive.** Listing is cheap; reading one compressed
  member spools the whole archive for `unrar`, in every policy.
- **Request latency dominates TAR.** 297 sequential requests took 13 s on localhost. On
  S3 or HTTP over a WAN, each request costs tens of milliseconds before its first byte.

## Decisions

### D1. How a caller hands archivey a remote archive

**Decision: `open_archive()` accepts an `fsspec.core.OpenFile`**, the object
`fsspec.open(url, **storage_options)` returns before it is entered. archivey opens it
itself, with fsspec's cache off, reads it through its own fetch planner (D2), and closes
what it opened. `source` sequences accept `OpenFile` items for an explicit volume list.

```python
import fsspec, archivey
with archivey.open_archive(fsspec.open("s3://bucket/set.7z", anon=True)) as ar:
    ar.read("data.csv")
```

Why this shape:

- **It keeps the maintainer's rule.** archivey still does not accept a URL. The caller
  builds the `OpenFile` and chooses the protocol, credentials and options; archivey
  never turns a string into a network request. A server that passes user-supplied file
  names to `open_archive()` cannot be made to fetch a URL.
- **DR-14: extend an existing option before adding a new one.** `source` already takes
  paths, streams and sequences. An `OpenFile` is one more kind of source, and the reader
  owns it the way it owns a path's handle.
- **It gives archivey what it needs and a file object does not:** the file system and
  the path. With those, archivey fetches ranges directly (`fs.cat_file(path, start,
  end)`), learns the size once (`fs.size(path)`), and lists the parent directory to find
  sibling volumes.
- **The check costs nothing without fsspec.** `open_archive()` tests for `OpenFile` only
  when `"fsspec" in sys.modules`. A caller who has not imported fsspec cannot be holding
  an `OpenFile`.

A caller who passes an already-open fsspec file (the object `fs.open(path)` returns)
keeps today's behaviour: archivey reads it as a seekable stream, through the caller's own
cache, and does not close it. archivey does not reach past the caller's object to its
file system: the caller chose that object and its cache, and a file object whose bytes
differ from `fs.cat_file` (a transforming subclass) would then read wrong data (DR-1).
The docs say which form to prefer and why.

Rejected:

- **URL strings in `open_archive()`.** Against the 2026-10-09 rule, and a server-side
  request forgery risk for every caller that passes untrusted names.
- **A separate `archivey.fsspec.open_url(url, storage_options=...)`.** It is
  `open_archive(fsspec.open(url, **opts))` under a second name (DR-14), and it would
  need its own copy of every `open_archive()` keyword.
- **Reading a caller's open fsspec file through its file system.** See above: it
  bypasses the caller's choice and can read the wrong bytes.

### D2. A fetch planner for remote sources

`ArchiveSource` gains one more way to supply its guarantees (`access-mode-and-cost`
§boundary table). For an `OpenFile` source, the raw stream is an internal
`RangeSource` over `(fs, path, size)`. Every byte reaches archivey through
`fs.cat_file(path, start, end)` (or `fs.cat_ranges` for D5). The planner decides each
fetch's start and length:

1. **Doubling read-ahead.** A read that continues where the last fetch ended doubles
   the next fetch, from 64 KiB up to `_MAX_FETCH` (8 MiB). A backward jump, or a forward
   jump past the read-through distance, resets it.
2. **Read through short forward gaps.** A forward seek of at most `_READ_THROUGH`
   bytes (1 MiB to start; tuned by the benchmark) does not end the sequential run: the
   planner keeps fetching through the gap. This is the TAR fix without any TAR code: a
   hop over a small member's data costs bytes, not a request, and a hop over a large one
   is a jump.
3. **Range hints from the backend.** A backend that knows what it will read next says
   so: `source.will_read(start, end)`. The planner fetches that span in one request, up
   to `_MAX_FETCH`, and then continues it as a sequential run. The hint never changes
   what bytes are returned, only how they are fetched. On a local source it does nothing.

   | Backend | Hint |
   | --- | --- |
   | ZIP, open | The last `min(size, 64 KiB + 22)` bytes: the end record, and the whole central directory when it fits |
   | ZIP, open, larger directory | The central directory's span, from the end record |
   | ZIP, member | `[local header, next local header or central directory)`: header, data and data descriptor in one request |
   | 7z, open | The next-header span named by the signature header |
   | 7z, member | The pack stream span, from pack info; for a solid folder, from the folder start |
   | ISO, member | The member's extent |
   | RAR, listing | The next header's position as each block is parsed (it lets the planner tell a header hop from a jump) |
   | TAR | None; rule 2 covers it |

4. **Bounded memory.** The planner keeps at most `_MAX_WINDOWS` (4) fetched windows,
   least recently used first out, so the read-ahead memory per source is at most
   32 MiB whatever the archive. Four windows let up to four concurrent member streams
   (`concurrent_operations=True`) read without evicting each other. A hinted span longer
   than `_MAX_FETCH` is fetched in `_MAX_FETCH` steps as it is read, not at once.

The constants are internal. They are fixed bounds that do not grow with input, so
DR-9 does not ask for a new public limit. If the benchmark or a user shows that a
caller needs to change them, that is a new public field then, not now.

What the planner should reach on the measured corpus (these become the benchmark
baselines, then the spec's ceilings):

| Archive | List | List and read one member |
| --- | --- | --- |
| ZIP, directory within the last 64 KiB | 2 GETs (head for detection, tail) | 3 |
| 7z, non-solid | 2 | 3 |
| 7z, solid | 2 | 2 + about log2(prefix / 64 KiB) |
| RAR5 | at most 3 for the header walk | unchanged until D6 |
| TAR | about archive size / 8 MiB, plus the jumps over members larger than 1 MiB | the same |
| tar.gz, tar.xz | about archive size / 8 MiB | the same |

Rejected:

- **Pick an fsspec cache per format.** The table shows every cache is wrong somewhere,
  and none of them can take a hint. The planner is about 200 lines and owns the policy.
- **A public `advise()` on archivey streams.** Callers never need to say this; backends
  do.

### D3. `ArchiveyFileSystem`

A subclass of `fsspec.archive.AbstractArchiveFileSystem`, protocol `archivey`,
registered through the base package's `fsspec.specs` entry point, so it exists for
fsspec as soon as archivey is installed, without an import.

```python
ArchiveyFileSystem(
    fo,                          # an fsspec URL, an OpenFile, a path, or a binary file object
    *,
    target_protocol=None,        # fsspec's chained-URL arguments, as zip:// takes them
    target_options=None,
    format=None, password=None, encoding=None, config=None,
    **kwargs,                    # passed to AbstractFileSystem
)
```

- **It opens the archive once,** with `seekable_members=True` and
  `concurrent_operations=True`, because fsspec consumers seek (pyarrow reads Parquet
  footers first) and open several files at once (dask). A string `fo` goes through
  `fsspec.open(fo, protocol=target_protocol, **target_options)` and then D1, so a chained
  URL gets the fetch planner.
- **The tree is the extraction view.** A path in the file system is the path
  `extract_all()` would write under the configured `ExtractionPolicy`: names normalized
  by `archivey.paths`, absolute names re-rooted, the current entry of a duplicate name,
  directories implied by member paths. A member that extraction refuses is not in the
  tree, and the refusal is a diagnostic on the reader, readable as `fs.reader.diagnostics`.
  The reason is DR-5: the same archive gives one result. A pandas user must not see a
  file that `archivey extract` would not write, or the other way round.
- **Links.** A link whose target resolves to a member appears as that member's type,
  with `"islink": True` and `"destination"` in its info, and opens the target (archivey's
  transparent link following). A link that resolves to nothing is listed with type
  `"other"`; opening it raises `FileNotFoundError`.
- **`info()`** carries fsspec's `name`, `size` and `type`, plus `mtime` (POSIX seconds)
  when the member has a time, and `member`, the `ArchiveMember`. `size` is the member's
  declared size, or `None` when the format does not record one before the data.
- **Read-only.** Every write method and every non-read `open` mode raises
  `NotImplementedError`, as fsspec's other read-only file systems do.
- **Errors.** A missing path raises `FileNotFoundError` and a directory opened for
  reading raises `IsADirectoryError`. archivey's own errors pass through unchanged
  (DR-15a), so a damaged member still raises `CorruptionError` from `read()`.
- **Pickling.** dask sends file systems to worker processes. The file system pickles
  as its constructor arguments and opens the archive again on the other side. It
  pickles when `fo` is a URL or an `OpenFile` and the archivey arguments pickle; a file
  object `fo` raises `TypeError` naming the reason.
- **Instance caching.** fsspec reuses a file system built with the same arguments.
  `cachable` stays `True`, so dask tasks that open the same URL share one listing,
  and the reader is safe for that because it is opened with `concurrent_operations=True`.
  `ZipFileSystem` sets `cachable = False` because `zipfile` is not thread-safe; that
  reason does not apply here.

Nested archives need nothing extra: `archivey://a.csv::archivey://inner.zip::s3://b/outer.tar`
chains two file systems, and the inner one reads a seekable member stream of the outer.
A compressed outer member re-decodes on a backward seek, with archivey's usual
loud-slow-rewind warning.

Rejected:

- **Registering `zip`, `tar` or `7z` protocols.** fsspec owns the first two, and taking
  any of these names would change what other people's URLs mean.
- **Showing raw member names.** The tree would then contain `../x` and duplicate paths,
  which no file system can hold, and would differ from what extraction writes.

### D4. Sibling volumes over fsspec

For an `OpenFile` source, volume discovery lists the parent directory once
(`fs.ls(parent, detail=False)`) and applies the same naming rules as local discovery
(`archive-reading` §Multi-volume). `discover_volume_siblings` is split so that the rules
run on a list of names, and the local and fsspec callers each supply the list. Each
volume becomes its own `RangeSource`. A file system without listing (plain HTTP) finds no
siblings, and a multi-volume open then fails as it does today for a lone first volume;
an explicit sequence of `OpenFile` objects is the way to open those.

### D5. Batched fetches for whole-archive passes

`extract_all()` and `stream_members()` over a non-solid ZIP or 7z know every member span
before they start. With the spans as hints, the planner can coalesce adjacent spans
into one request and prefetch the next span while the current one decodes, through
`fs.cat_ranges`, which fsspec runs concurrently for async file systems. This is a later
stage: the planner of D2 already turns each member into one request, and the benchmark
will show whether the remaining latency matters.

### D6. RAR members over a remote source

Reading a compressed RAR member needs `unrar`, which needs a file. Today the whole
archive is spooled, bounded by `SpoolLimits.max_bytes` (1 GiB) and announced in
`CostReceipt.notes`. Over a remote source that copy is a full download.

A **sparse spool** could cut it: a temporary file of the archive's full length that holds
only the bytes `unrar` reads (every block header, whose offsets the native parser
already knows, and the target member's data; for a solid archive, the data of every
member before it), with holes elsewhere. Whether `unrar` reads anything else (a
recovery record, the quick-open data, the end-of-archive block) has to be measured
first, with a test that fills the holes with garbage and checks that the output and the
CRC verdict do not change. This is an investigation task, not a commitment. Until it
lands, the cost note stays as it is.

### D7. A changed remote object

An object can be replaced between two range requests (an S3 overwrite during a long
read). A ranged read then returns bytes of a different archive, and the CRC catches it
only for data that has a checksum. When the file system can pin a version (s3fs with
`version_aware=True`, gcsfs generations), `RangeSource` pins the version it saw at open.
Otherwise it compares the total size on each fetch whose response carries it, and stops
on a change. Which error that raises is open question 2.

## Benchmark: requests and bytes fetched

The structural gate counts seeks today. This change adds a remote axis.

- **`benchmarks/remote.py`** defines `CountingFileSystem`, an fsspec file system over
  local fixture files. It records each `cat_file` / `cat_ranges` range, each `info` /
  `size` call and each `ls`. It needs no network and is deterministic, so its counts are
  exact on every host.
- **Cases**, per format: open and list; read the first, a middle and the last member;
  `extract_all()`; one `stream_members()` pass under `streaming=True`; and one read
  through `ArchiveyFileSystem` (`fsspec.open("archivey://m::counting://fixture")`).
- **Recorded per case:** `range_requests`, `metadata_requests`, `bytes_fetched`, and
  `bytes_needed`, the least the operation can read (the index bytes plus the member's
  stored span, or the whole archive for a compressed TAR). `overfetch = bytes_fetched /
  bytes_needed` is the figure to watch.
- **Gate:** `range_requests` exact and `bytes_fetched` at most the baseline, in
  `benchmarks/baselines/remote.json`, updated with `--update-baselines` like
  `structural.json`. Both are structural invariants, so they block a PR.
- **Fixtures** must be larger than the planner's windows, or every case is one request.
  The generator in `benchmarks/fixtures.py` gains a "remote" scale: about 300 members and
  20 to 40 MB per archive, built at run time, never committed.
- **Informational:** a modelled time, `range_requests x 30 ms + bytes_fetched / 50 MB/s`,
  printed beside each case so that a change that trades requests for bytes can be judged.
  Not gated.
- **A real-HTTP check, off the PR path.** `benchmarks/remote_http.py` runs the same cases
  against a local range server through fsspec's `HTTPFileSystem`. Its counts must equal
  the counting file system's; a difference means the model misses something the real
  path does. It also records the "caller's own fsspec file with the default cache"
  numbers, which the docs quote.

## Open questions

1. **The CLI and URLs.** `archivey list s3://bucket/set.7z` is the obvious use. The
   proposal: when fsspec is installed, an argument with a `scheme://` prefix that fsspec
   knows, and that is not an existing local path, is opened as `fsspec.open(arg)`.
   The CLI is a user tool, so the server-side concern of D1 does not apply, but this is a
   public behaviour and goes to the maintainer.
2. **D7's fallback.** When no version can be pinned, should a size change between
   fetches raise, and with which error? `OpenError` fits a change seen at open,
   `CorruptionError` one seen later. Settled at implementation against DR-1.
3. **Download as a cost.** Bytes fetched cost money on metered egress. `CostReceipt`
   describes access cost in kinds, not bytes. A "this operation downloads about N bytes"
   figure would need the planner to predict, which it can for ZIP and 7z and not for
   TAR. Left for when a user asks.
