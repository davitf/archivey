## MODIFIED Requirements

### Requirement: Opening an archive for reading

The system SHALL expose:

```python
archivey.open_archive(
    source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO],
    *,
    format: ArchiveFormat | None = None,
    streaming: bool = False,
    seekable_members: bool = False,
    concurrent_members: bool = False,
    password: PasswordInput = None,
    encoding: str | None = None,
    config: ArchiveyConfig | None = None,
) -> ArchiveReader
```

`source`, multi-volume ordering, `streaming`, password candidates/providers,
encoding, configuration precedence, and backend selection retain their existing
contracts. `format=None` auto-detects; an explicit format bypasses detection.

**Diagnostics at open (observable):** On success, advisory events from automatic
detection (if any) appear in this reader's cumulative `diagnostics` for its
lifetime and are not duplicated. Explicit `format=` skips detection, so open
adds no detection diagnostics. If open raises, no reader is returned.

Handoff mechanics (one shared collector/budget, no copy/re-seed): see
`format-detection` and `diagnostics`.

#### Scenario: open matrix

| Case | Expected |
| --- | --- |
| Auto-detect succeeds | Detection events visible on `reader.diagnostics`; not duplicated |
| `format=ArchiveFormat.ZIP` succeeds | No detection diagnostics from open |
| Open raises | No reader returned |
| `password="secret"` | Returned reader uses that password for encrypted members |

### Requirement: Declared member-stream capabilities

`open_archive()` SHALL accept two keyword-only booleans, both defaulting to `False`:

- `concurrent_members=True` — any number of member streams may be open simultaneously
  (full contract: `reader-concurrency`)
- `seekable_members=True` — seekable where the backend can provide it

The system SHALL NOT expose a flag-enum parameter for this purpose. `open_stream` SHALL
keep its `seekable: bool` parameter, and both entry points SHALL use the same `seekable`
vocabulary for the same concept; concurrency has no meaning for a single standalone
stream, so `open_stream` MUST NOT gain a concurrency parameter.

The `MemberStreams` flag type SHALL remain publicly exported and SHALL remain the value
reported for declared capabilities on `CostReceipt` and in diagnostics. It is no longer
an input to `open_archive`.

**Default (neither declared), every format including directory:** at most one live member
data stream per reader; streams are forward-only. "Live" spans `open()` →
stream `close()`/context exit (not EOF, not GC). A second overlapping `open()`
SHALL raise `ConcurrentAccessError` at the later call and leave the first stream
untouched/readable — never silently close/invalidate a held stream. Every member
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
