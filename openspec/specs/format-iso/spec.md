# ISO 9660 Archive Support

## Purpose

ISO 9660 disc images are read through the unified `ArchiveReader` API using
`pycdlib` from the optional `[recommended]` extra. The backend selects the richest
available filename/metadata namespace and reports that choice so callers can
reason about fidelity.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Reader API, member metadata, declared member-stream capabilities |
| `access-mode-and-cost` | Indexed/direct cost model and seekability requirements |
| `format-detection` | ISO magic at `CD001` offset and extended peek window |
| `reader-concurrency` | `MemberStreams.CONCURRENT`, operation ownership, lock boundaries |
| `packaging-and-extras` | Optional `pycdlib` (`[recommended]`) availability |

## Requirements

### Requirement: Declare ISO format properties

The ISO backend SHALL expose these properties for every opened ISO image:

| Property | Value |
| --- | --- |
| Backend dependency | `pycdlib` |
| Listing cost | `ListingCost.INDEXED` — directory tree in header/catalog region |
| Access cost | `AccessCost.DIRECT` |
| Stream capability | `StreamCapability.SEEKABLE` |
| Read source | Seekable only |
| Write support | No; ISO writing is out of scope |

Write attempts SHALL raise `UnsupportedOperationError`. Non-seekable read
sources SHALL be rejected at open because `pycdlib` requires seeking; the backend
MUST NOT implicitly buffer or copy the image to make it seekable.

#### Scenario: ISO property matrix

| Case | Expected |
| --- | --- |
| Open valid ISO | `cost.listing_cost=INDEXED`, `cost.access_cost=DIRECT`, `cost.stream_capability=SEEKABLE` |
| Attempt to create/write ISO | `UnsupportedOperationError` |
| Open from non-seekable source | Seekability error at open; no implicit buffering |

### Requirement: Auto-select the richest available namespace

The ISO backend SHALL select the richest available namespace in priority order:
Rock Ridge, then Joliet, then plain ISO 9660. It SHALL report the selection in
`ArchiveInfo.extra["iso.namespace"]`.

| Namespace | Reported value | Filename fidelity | POSIX metadata |
| --- | --- | --- | --- |
| Rock Ridge | `"rock_ridge"` | Original case and full length | mode, uid, gid, symlinks |
| Joliet | `"joliet"` | Case-preserved, up to 64 UCS-2 chars | none |
| Plain ISO 9660 | `"iso9660"` | Upper-case 8.3 / level-1 names | none |

Fields unavailable in the selected namespace SHALL be `None`.

#### Scenario: ISO namespace matrix

| Case | Expected |
| --- | --- |
| Image contains Rock Ridge | Use Rock Ridge names/metadata; `iso.namespace="rock_ridge"` |
| Image contains Joliet but no Rock Ridge | Use Joliet names; POSIX fields `None`; `iso.namespace="joliet"` |
| Image contains neither extension | Use plain ISO 9660 names; POSIX fields `None`; `iso.namespace="iso9660"` |
| Rock Ridge symlink | Symlink metadata is available through the selected namespace |

### Requirement: Serialize shared pycdlib handle operations for concurrent reads

For ISO readers that allow concurrent member streams under
`MemberStreams.CONCURRENT`, the backend SHALL keep using `pycdlib` payload APIs
(for example `open_file_from_iso`) and SHALL serialize every operation that
touches `pycdlib`'s shared image handle with one per-reader lock. This preserves
pycdlib extent and namespace behavior while preventing races on shared state.

The lock SHALL cover `PyCdlib.open()` / `open_fp()` initialization and failure
cleanup, `open_file_from_iso()` and `PyCdlibIO.__enter__`, member `read` /
`readinto` / supported `seek` / `tell`, member close/context exit,
archive/PyCdlib close, and any audited operation that repositions or closes
`PyCdlib._cdfp` or `PyCdlibIO._fp`. Archivey buffering/error/lifecycle wrappers
sit outside it; exception translation, diagnostics/logging, lifecycle release,
callbacks, and finalizers run after the lock is released. Unsupported positioning
retains normal `io.UnsupportedOperation` behavior.

For the pinned `pycdlib` implementation, `walk()` and `get_record()` SHALL be
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
| Workers concurrently call member `open()` | `open_file_from_iso` and `PyCdlibIO.__enter__` execute under the same per-reader lock as stream operations |
| Independent streams read/readinto/close and use supported positioning | Complete pycdlib operations serialize; member positions remain correct |
| Materialization uses pinned `walk()` / `get_record()` | Regression probe confirms catalog-only behavior; future handle access receives the lock |
| Operation raises or closes | Translation/logging/lifecycle/callback work runs without the ISO handle lock held |
| Future throughput optimization proposed | Evidence compares wall/lock timing and practical seek/byte counters; adds peak memory only if buffering/materialization changes |

### Requirement: Refuse raw CD sector images by name

The ISO backend SHALL recognise a raw CD sector image — a dump whose sectors begin with
the 12-byte sync pattern `00 FF×10 00`, as the `.bin` of a `.bin`/`.cue` pair does —
and SHALL refuse it with `UnsupportedFeatureError` naming the layout found, rather than
read it or let detection fail. Detection SHALL claim such a file as `ISO` from the sync
pattern at offset 0 so that the refusal is reachable, and the refusal SHALL NOT depend on
`pycdlib` being installed.

The layout named SHALL be the sector mode (Mode 1, Mode 2 Form 1, Mode 2 Form 2, or an
unknown mode byte) and, when a second sync is found, the sector size: 2352, or 2448 for a
dump carrying subchannel data. Reading a raw image by stripping sectors to their payload
is deferred past 0.2.0 and documentation SHALL NOT claim it.

#### Scenario: raw-sector matrix

| Case | Expected |
| --- | --- |
| Raw Mode 1 or Mode 2 Form 1 image, 2352- or 2448-byte sectors | `UnsupportedFeatureError` naming the layout and pointing at converting to `.iso` |
| Raw Mode 2 Form 2 image | `UnsupportedFeatureError` saying it holds no ISO 9660 filesystem |
| Unknown mode byte | `UnsupportedFeatureError` naming the mode |
| `detect_format` on a raw image | `ISO`, `CERTAIN`, `detected_by="magic"` |
| `pycdlib` not installed | The same refusal |
| Plain `.iso` | Unaffected: it starts with the zero-filled system area, never the sync |
