## MODIFIED Requirements

### Requirement: Declared member-stream capabilities

`open_archive()` SHALL accept two keyword-only booleans, both defaulting to `False`:

- `concurrent_members=True` — any number of member streams may be open simultaneously
  (full contract: `reader-concurrency`)
- `seekable_members=True` — seekable where the backend can provide it

The system SHALL NOT expose a flag-enum parameter for this purpose. `open_stream` SHALL
keep its `seekable: bool` parameter, and both entry points SHALL use the same `seekable`
vocabulary for the same concept; concurrency has no meaning for a single standalone
stream, so `open_stream` MUST NOT gain a concurrency parameter.

The `MemberStreams` flag type SHALL remain publicly exported as the internal
representation the booleans map to at the entry point. It is no longer an input to
`open_archive`. It is NOT required to appear on `CostReceipt` or in diagnostics —
neither carries it, and no requirement SHALL claim otherwise. Whether the declared
capabilities become a typed part of the `ArchiveReader` contract (today
`member_streams` exists only on the concrete base class) is deliberately left open.

**Default (neither declared), every format including directory:** at most one live member
data stream per reader; streams are forward-only. "Live" spans `open()` →
stream `close()`/context exit (not EOF, not GC). A second overlapping `open()`
SHALL raise `ConcurrentAccessError` at the later call and leave the first stream
untouched/readable — the gate never resolves contention by closing a held stream.
(This is a rule about *contention*, not lifetime: `reader.close()` does close
member streams — see "Context-manager and close lifecycle".) Every member
stream (random `open()` and `stream_members()` yields) SHALL report
`seekable() is False`; `seek()` SHALL raise `io.UnsupportedOperation`; `tell()`
SHALL work. Sequential `open → read → close → open next` is unaffected.

`ConcurrentAccessError`'s message SHALL name the parameter a caller would pass to
allow the operation (`concurrent_members=True`), not an internal type.

`open_archive()` SHALL capture the caller stack once; `ConcurrentAccessError`
SHALL include that `file:line`. Full stack is retained on the reader for
diagnostics (no config knob). Capabilities are per-archive intent only — no
`ArchiveyConfig` equivalent, no per-`open()` flag. Access cost never determines
legality; the cost receipt describes expense.

**Internal ops exempt:** `extract_all()` (incl. hardlink recovery), symlink-target
reads, password confirmation, and other library-internal opens run under internal
scopes and need no declared capability.

**Out of gate scope:** non-overlapping open *order* on solid archives (each
re-decode from block start) stays under `AccessCost` / `solid_block_count` /
`stream_members()` steer. Docs for the capability booleans SHALL state this.

#### Scenario: capability gate matrix

| Case | Expected |
| --- | --- |
| Overlapping second `open()` without `concurrent_members` (ZIP/TAR/ISO/single-file/dir) | `ConcurrentAccessError` at later `open()` with open_archive `file:line`; first stream remains readable |
| Non-overlapping open/read/close loop, no capabilities declared | All opens succeed |
| Stream without `seekable_members` (incl. real directory file) | `seekable()` false; `seek()` → `io.UnsupportedOperation`; `tell()` + forward reads OK |
| Same member with `seekable_members=True` | Seekable where backend provides it; loud-slow-rewind rule for non-accelerated path |
| `extract_all()` with nothing declared | Completes; internal opens ungated |
| `open_archive(p, member_streams=...)` | `TypeError` — the parameter no longer exists |

### Requirement: Context-manager and close lifecycle

The reader SHALL implement `__enter__` / `__exit__` / `close()`. `close()` SHALL
be idempotent.

**Caller observables:**

- Exiting `with open_archive(...)` closes the reader.
- After reader close, every new reader operation or property (including
  `__enter__`, iteration/listing/lookup, metadata/cost, `open`/`read`,
  `stream_members`, extraction) SHALL raise `ArchiveyUsageError`. Repeated
  `close()` / `__exit__` are no-ops.
- `close()` SHALL close every member stream still open on that reader, in the
  order they were opened, and SHALL do so only after the reader has actually
  transitioned to closed (so a `close()` that raises leaves streams untouched).
  A member stream SHALL NOT outlive its reader — reading one afterwards raises
  as it would for any closed stream. This matches `zipfile.ZipFile.close()` and
  `tarfile.TarFile.close()`.
- Each stream close releases that stream's lease, so backend teardown still runs
  once, after the last stream closes — the source is never torn down underneath
  a stream still reading through it.
- A member-stream close failure SHALL NOT prevent the remaining streams from
  being closed; a single failure propagates, several surface as a
  `BaseExceptionGroup`.
- Archivey SHALL never close a caller-supplied `BinaryIO`. If the caller closes
  it early, a later operation raises `ArchiveyUsageError` for the closed source;
  concurrent external close with I/O is unsupported.
- `__exit__` always calls `close()`. Close failure propagates on normal exit;
  during body-exception unwind the body exception remains via normal chaining.

**Under `MemberStreams.CONCURRENT`:** `reader.close()` drains in-flight worker
`open()`/`read()` before transitioning to closed (see `reader-concurrency`).
Without `CONCURRENT`, concurrent close with an actively executing worker call is
rejected.

Lease/token/teardown once-guards and dual-failure `ExceptionGroup` rules:
`reader-concurrency`.

#### Scenario: lifecycle matrix

| Case | Expected |
| --- | --- |
| Open stream, then close reader (no concurrent I/O) | New reader ops → `ArchiveyUsageError`; the stream is closed by that `close()`; backend released after it |
| Idle open stream + `reader.close()` | Close succeeds and closes the stream; a later read raises; `stream.close()` is a no-op |
| Several open streams + `reader.close()` | All are closed; teardown runs once, after the last |
| `close()` raises (active pass/worker) | Reader stays open; member streams untouched |
| Stream dropped without close | Finalizer reclaims it; the stream must not be kept alive by its own finalizer |
| Caller-supplied `BinaryIO`, all closed | Library does not call `close()` on that source |
| `open_archive()` context exits | Reader closed; any member stream still open is closed with it, then the backend is released |
| Op after reader close | `ArchiveyUsageError` |
