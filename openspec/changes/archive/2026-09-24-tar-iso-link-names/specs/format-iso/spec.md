## ADDED Requirements

### Requirement: List every ISO directory record as its own member

The ISO backend SHALL walk directory records, not names: each record is one
member, and its name is a rendering of the record rather than a key looked up
again. A name that does not round-trip through pycdlib's own path lookup (a Rock
Ridge name holding `/`, two entries sharing one Rock Ridge name) SHALL NOT cost
any other member, and each directory extent SHALL be descended at most once.

Member type SHALL come from the Rock Ridge PX mode when one is present: a
directory or symlink as already recognised, a regular file as `FILE`, and any
other file type (device node, FIFO, socket) as `OTHER` with `size=None`.

In the plain ISO 9660 namespace the `;N` file version SHALL be removed from the
presented name together with the `.` of an empty extension (`FOO.;1` is `FOO`),
and recorded as `extra["iso.version"]`. When a directory holds several versions
of one name, the highest takes the bare name and the others SHALL be presented
as `name;N` with `is_current=False`, the RAR file-version history shape.

In the Rock Ridge namespace a record whose System Use area carries no Rock Ridge
entries SHALL still be listed, under its ISO 9660 identifier (version and
empty-extension dot removed), with `MEMBER_HEADER_RECORD_SKIPPED` emitted and
attached to it.

In the Rock Ridge namespace the relocation directory (a root-level directory
every child of which is a relocated directory, its `..` carrying a PL record)
SHALL NOT be listed; the relocated subtrees appear at their logical place.

#### Scenario: ISO record matrix

| Case | Expected |
| --- | --- |
| Rock Ridge name `a/a` beside `bbb` | Both list; `bbb` reads |
| Two directories with Rock Ridge name `dup` | Both list as `dup/` |
| PX mode `0o020666` (char device) | `type=OTHER`, `size=None`; extraction skips it |
| Plain `FOO.;1` and `FOO.;2` | `FOO` (version 2, current) and `FOO;1` (version 1, `is_current=False`); extraction writes version 2 |
| Rock Ridge image, one record with its System Use area zeroed | Listed under its ISO 9660 name; `MEMBER_HEADER_RECORD_SKIPPED` attached |
| Directory record pointing back at an ancestor extent | Listed once there; not descended again |
| Rock Ridge tree 12 directories deep | Logical tree lists in full; no `rr_moved` member |

## MODIFIED Requirements

### Requirement: Serialize shared pycdlib handle operations for concurrent reads

For ISO readers that allow concurrent member streams under
`MemberStreams.CONCURRENT`, the backend SHALL keep using `pycdlib` payload APIs
(a `PyCdlibIO` built from the member's directory record) and SHALL serialize every operation that
touches `pycdlib`'s shared image handle with one per-reader lock. This preserves
pycdlib extent and namespace behavior while preventing races on shared state.

The lock SHALL cover `PyCdlib.open()` / `open_fp()` initialization and failure
cleanup, `PyCdlibIO` construction and `PyCdlibIO.__enter__`, member `read` /
`readinto` / supported `seek` / `tell`, member close/context exit,
archive/PyCdlib close, and any audited operation that repositions or closes
`PyCdlib._cdfp` or `PyCdlibIO._fp`. Archivey buffering/error/lifecycle wrappers
sit outside it; exception translation, diagnostics/logging, lifecycle release,
callbacks, and finalizers run after the lock is released. Unsupported positioning
retains normal `io.UnsupportedOperation` behavior.

For the pinned `pycdlib` implementation, the root `get_record()` and the
directory-record child enumeration (`pycdlib.pycdlib._yield_children`) SHALL be
treated as audited in-memory catalog operations under the materialization owner
scope. Regression tests SHALL record that audit; if a supported `pycdlib` version
adds handle access, the affected call joins the critical section.

The lock guarantees correctness but not parallel throughput. Any later
independent-handle or raw-extent optimization SHALL use targeted before/after
measurements; this baseline has no correctness speed threshold.

#### Scenario: ISO handle-lock matrix

| Case | Expected |
| --- | --- |
| Two ISO file members opened/read interleaved | Each stream yields exact bytes in order |
| Multiple threads read distinct members under `MemberStreams.CONCURRENT` after materialization | No data races on shared `pycdlib` handle |
| Workers concurrently call member `open()` | `PyCdlibIO` construction and `__enter__` execute under the same per-reader lock as stream operations |
| Independent streams read/readinto/close and use supported positioning | Complete pycdlib operations serialize; member positions remain correct |
| Materialization uses pinned root `get_record()` / `_yield_children()` | Regression probe confirms catalog-only behavior; future handle access receives the lock |
| Operation raises or closes | Translation/logging/lifecycle/callback work runs without the ISO handle lock held |
| Future throughput optimization proposed | Evidence compares wall/lock timing and practical seek/byte counters; adds peak memory only if buffering/materialization changes |
