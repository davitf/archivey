## MODIFIED Requirements

### Requirement: Declared member-stream capabilities

`open_archive()` SHALL accept two keyword-only booleans, both defaulting to `False`:

- `concurrent_members=True` — any number of member streams may be open simultaneously
  (full contract: `reader-concurrency`)
- `seekable_members=True` — every member stream from random `open()` is seekable

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
start (loud-slow-rewind). `stream_members()` yields are a single-pass decode;
SEEKABLE does not require those handles to seek.

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
| `extract_all()` with nothing declared | Completes; internal opens ungated |
| `open_archive(p, member_streams=...)` | `TypeError` — the parameter no longer exists |
| Seek before the start of a random `open()` stream with `seekable_members=True` | Relative (`SEEK_CUR` / `SEEK_END`) underflow clamps to 0, as `io.BytesIO`; negative `SEEK_SET` or unknown `whence` → `ValueError`, never a translated archive error. Directory member: relative underflow is the OS file's `OSError` |

### Requirement: Listing metadata-byte accounting

The system SHALL measure `max_metadata_bytes` as a **safety-oriented weight** of
retained string/bytes fields accumulated as members are registered, plus
archive-level `ArchiveInfo.comment` once when known. Exact UTF-8 encoding of
every field is not required — the cap exists to bound metadata bombs, not to
mirror an allocator — but the weight MUST NOT under-count UTF-8 size:

- `str` fields `name`, `comment`, `link_target`, `uname`, `gname`: a cheap
  upper bound on UTF-8 length — `len(s)` when `s` is ASCII, otherwise
  `4 * len(s)` (UTF-8 is at most 4 bytes per code point). Implementations MAY
  use a stricter exact encode; they MUST NOT use a measure that can be smaller
  than UTF-8 (plain `len(s)` on non-ASCII would under-count a Unicode name bomb).
- `raw_name`: `len(raw_name)` when not `None` (stored archive bytes; already exact)
- `extra`: lengths of `str` / `bytes` values under the same rules; for a one-level
  `dict` value, its `str` / `bytes` keys and values (archive-sized text such as TAR's
  PAX keywords). Top-level `extra` keys are format-defined literals and are not counted
- Exclude: `_raw`, `hashes`, diagnostics, Python object overhead

A field filled in after its member was registered SHALL be weighed when it is filled
in, under the same enforcement as registration. The case that exists is a symlink
target stored as member data (ZIP, 7z, RAR3/4), which is read only once every member is
registered: a target resolved while materializing `members()` / `scan_members()` SHALL
count toward `max_metadata_bytes` before that list is published.

A field that is expanded before any member is registered MAY be weighed by the size
its header declares, before it is expanded, when expanding it is itself the cost to
bound. The case that exists is RAR 1.5/2.x compressed old-style comments, each decoded
by a separate `unrar` process: `format-rar` sums their declared unpacked sizes at
`open_archive` and refuses the archive before decoding any. That check is separate
from the running total above; the decoded comments are weighed again at registration.

#### Scenario: metadata accounting matrix

| Case | Expected |
| --- | --- |
| Member with long `name` + `raw_name` | Both weights count |
| Huge `ArchiveInfo.comment` alone | Counts toward the budget once |
| `extra` holds opaque non-str/bytes object | Not counted, and neither is its top-level key |
| `extra` holds a dict with a long key (PAX keyword) | The key counts, like its value |
| ASCII-only name | Weight equals `len(name)` (exact UTF-8) |
| Non-ASCII / surrogateescape name | Weight ≥ UTF-8-with-surrogateescape byte length (upper-bound OK) |
| Symlink target read from member data after registration | Weighed when read; over the cap → `ResourceLimitError` naming `max_metadata_bytes` |
| RAR compressed old-style comments whose declared sizes sum past the cap | Refused at `open_archive` before any is decoded (`format-rar`) |
