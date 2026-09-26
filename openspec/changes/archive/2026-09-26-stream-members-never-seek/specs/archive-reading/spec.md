## MODIFIED Requirements

### Requirement: Declared member-stream capabilities

`open_archive()` SHALL accept two keyword-only booleans, both defaulting to `False`:

- `concurrent_members=True` — any number of member streams may be open simultaneously
  (full contract: `reader-concurrency`)
- `seekable_members=True` — every member stream from random `open()` is seekable;
  `stream_members()` yields stay forward-only (below)

The system SHALL NOT expose a flag-enum parameter for this purpose. `open_stream` SHALL
keep its `seekable: bool` parameter, and both entry points SHALL use the same `seekable`
vocabulary for the same concept; concurrency has no meaning for a single standalone
stream, so `open_stream` MUST NOT gain a concurrency parameter.

The `MemberStreams` flag type is the internal representation the booleans map to at
the entry point. It SHALL stay importable from `archivey.types` for backends and tests
and SHALL NOT be re-exported from `archivey` or listed on the API page. It is no longer
an input to `open_archive`. It is NOT required to appear on `CostReceipt` or in
diagnostics — neither carries it, and no requirement SHALL claim otherwise. No public
reader attribute SHALL expose the declared capabilities; the two booleans on
`open_archive` are the whole contract.

**Default (neither declared), every format including directory:** at most one live member
data stream per reader; streams are forward-only. "Live" spans `open()` →
stream `close()`/context exit (not EOF, not GC). A second overlapping `open()`
SHALL raise `ConcurrentAccessError` at the later call and leave the first stream
untouched/readable — the gate never resolves contention by closing a held stream.
The refusal SHALL happen before the member is opened: a refused `open()` SHALL NOT
construct a member data stream, spawn a helper process, or read member data.
(This is a rule about *contention*, not lifetime: `reader.close()` does close
member streams — see "Context-manager and close lifecycle".) Every member
stream (random `open()` and `stream_members()` yields) SHALL report
`seekable() is False`; `seek()` SHALL raise `io.UnsupportedOperation`; `tell()`
SHALL work. Sequential `open → read → close → open next` is unaffected.

With `seekable_members=True`, every file member stream from random `open()` SHALL
report `seekable() is True` and `seek()` SHALL work, including a backward seek
that returns the same bytes. The cost MAY be a full re-decode from the member
start (loud-slow-rewind).

A `stream_members()` handle SHALL report `seekable() is False` and its `seek()` SHALL
raise `io.UnsupportedOperation`, regardless of `seekable_members` and of `streaming`,
on every format including directory and single-file; `tell()` SHALL work. The pass is a
single-pass decode and owns the position: a seek would decode again behind the
iterator's back, and on a solid or piped member it cannot be done at all. The rule is
uniform so that callers cannot come to rely on a handle that seeks on some formats
only. A caller that needs to seek opens the member with random `open()` under
`seekable_members=True`.

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
| Refused second `open()` without `concurrent_members` | Raises before the member is opened — no member stream constructed, no helper process spawned, no member data read |
| Non-overlapping open/read/close loop, no capabilities declared | All opens succeed |
| Stream without `seekable_members` (incl. real directory file) | `seekable()` false; `seek()` → `io.UnsupportedOperation`; `tell()` + forward reads OK |
| Same member via random `open()` with `seekable_members=True` | `seekable()` true; backward seek rereads; loud-slow-rewind when there is no index/accelerator |
| `stream_members()` handle with `seekable_members=True`, `streaming=False` or `True`, file source, every format | `seekable()` false; `seek()` → `io.UnsupportedOperation`; `tell()` + forward reads OK |
| `extract_all()` with nothing declared | Completes; internal opens ungated |
| `open_archive(p, member_streams=...)` | `TypeError` — the parameter no longer exists |
