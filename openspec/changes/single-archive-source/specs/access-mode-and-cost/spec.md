## MODIFIED Requirements

### Requirement: Declaring access mode at open_archive()

`open_archive(..., streaming: bool = False)` SHALL accept exactly two modes:

| Mode | Meaning |
| --- | --- |
| `streaming=False` (default) | **Random access.** Load indexes when available. Fail fast at open if the source is non-seekable and the format cannot adapt — never silently degrade to forward-only. Seek points for single-stream formats are built **lazily** on first `seek()`. |
| `streaming=True` | **Forward-only, single pass.** Disable index loading where possible; works on non-seekable sources **where the backend reads front to back** (see below). Random-access / full-materialization APIs disabled **uniformly** (independent of any loaded index). `members_report_if_available()` stays callable (never scans). |

Non-seekable sources are never given random access: with `streaming=False` the
library fails fast at open when the format needs seek (it does not buffer the
source into memory or a temp file). `streaming=True` is the fix for pipes and
sockets **only where the backend reads front to back** (TAR, the single-file
compressors). A format that needs seek in either mode (ZIP, ISO, 7z, RAR) SHALL be
refused with one message naming a seekable source as the fix, in both modes, rather
than proposing a `streaming=True` retry the same call would then refuse.
Eager seek-point building is not exposed.

Every source `open_archive` and `open_stream` take SHALL cross one boundary, which
returns one `ArchiveSource`. It SHALL carry every source-level guarantee itself, whatever
the caller passed — a path, a stream, or a volume list. Every consumer of the raw source
SHALL receive one of three things: the `ArchiveSource`; a wrapper over it that preserves
the four guarantees below (a view that clamps its reads and does not close its inner);
or, for a third-party parser that opens a path itself, the `ArchiveSource`'s path.
Standalone `detect_format` does not cross this boundary:

| Guarantee | What `ArchiveSource` SHALL do |
| --- | --- |
| Full-count | `read(n)` returns `n` bytes unless the source is exhausted. A raw `read(n)` may legally return short, and header parsers, archivey's and the stdlib's alike, issue one `read(n)` and treat a short as EOF |
| Ownership | Close what archivey opened or built — a path's handle, a joined volume set, its own read buffer — and never the caller's object, including a caller stream inside a volume list |
| Bounded reads | No `read(n)` asks the source for more than it can still supply: clamped when the remaining length is a fact, served in steps when it is not, so a length an archive declares cannot become an allocation at the source |
| Cheap facts | The path when a real file exists, the volume paths of a joined set, the size when it is a fact, and the name, each settled once at the boundary |

How full-count is supplied differs by source, and the difference is read-ahead:

| Source | Read-ahead added at the boundary |
| --- | --- |
| Path | The file's own `io.BufferedReader`, opened on first read |
| Seekable stream that is already buffered (a `BytesIO`, an `open()` handle) | **None.** It is already full-count; `fileno()` still forwards |
| Seekable stream, not already buffered | A fixed-size read buffer. Bounded, and recoverable by seeking |
| Non-seekable stream that is already `io.BufferedReader` / `io.BufferedRandom` | **None.** The caller's buffer already supplies full-count and its read-ahead is the caller's |
| Non-seekable stream, not already a CPython buffer | **Only the detection prefix**, when detection ran: at most `DETECTION_LIMIT` bytes, filled by `peek` and drained by the first reads. Past it, missing bytes are re-asked for, so a `read(n)` takes exactly `n` from the source. Codec layers above it may still buffer |

Neither is the materialization forbidden above: `ArchiveSource` over a non-seekable
source SHALL hold no buffered bytes beyond the detection prefix, which never grows past
`DETECTION_LIMIT`, and SHALL report `seekable()` as `False`, so `streaming=False` over it
still fails fast at open.

This is how "archivey never closes a caller-supplied `BinaryIO`" (`archive-reading`) is
kept regardless of what a backend builds on top of the source: the caller's object is
never handed past the boundary, so no wrapper a backend adds can reach it except through
`ArchiveSource`, which borrows it.

#### Scenario: open mode matrix

| Case | Expected |
| --- | --- |
| `streaming=False` on indexed ZIP | Central directory loaded; random access available |
| `streaming=True` on `.tar.gz` | No full-archive index scan; members as stream is read |
| `streaming=False` on non-seekable source, backend reads front to back | Error at open (before member data) naming `streaming=True` — library does not buffer |
| Either mode on non-seekable source, backend needs seek | Same error and same message in both modes, naming a seekable source (buffer to disk or a `BytesIO`) — library does not buffer |
| Seekable stream source, either mode | Full-count `read(n)` from the `ArchiveSource`: a source that is not already buffered gets a fixed-size read buffer (bounded readahead only), and one that already is (a `BytesIO`, an `open()` handle) gets no readahead. Never materialized to memory or disk |
| Non-seekable stream source, `streaming=True` | The `ArchiveSource` gives full-count `read(n)` with no read-ahead beyond the detection prefix: `seekable()` stays `False`; reads drain the prefix first, and once it is drained (or when an explicit `format=` meant it was never filled) a `read(n)` on *that stream* takes exactly `n` bytes from the source. Codec layers above the boundary may still buffer — `DecompressorStream` wraps its input in a `BufferedReader`, so an end-to-end `read(20)` on a compressed non-seekable open takes `io.DEFAULT_BUFFER_SIZE` from the source (8 KiB through 3.13, 128 KiB from 3.14) |
| Non-seekable stream that is already `io.BufferedReader` | No second buffer is added; the caller's buffer already supplies full-count. `fileno()` forwards through the `ArchiveSource` |
| Non-seekable stream source, metadata probes | The `ArchiveSource` answers what the probes need: a source carrying `name` / `size` still answers `source_name` and `source_byte_size`, so `compressed_source_size` and `ResolvedSource.archive_name` do not degrade. It is not transparent in general — what a backend sees is the `ArchiveSource`'s surface, not the source's class, so `read1` / `detach` / `BytesIO.getvalue` do not survive it, and `peek` is the `ArchiveSource`'s own replay prefix, not the source's. `tell()` does not become available either — it raises, as the seek-required refusals depend on |
| Non-seekable short-returning source, any supported streaming format, with and without `format=` | Opens, lists, and reads identically to the full-count source — the guarantee does not depend on detection having run or on a third-party reader's internal buffering |
| Any stream source, every format, measurement on or off | The reader closing does not close the caller's stream, and the caller can still read from it. Holds for a failed open too: the backend releases what it opened, which never includes the caller's object |
| Archive whose header declares a length far past the source's end (a 4 GiB directory record in a 55 KiB ISO image), path and stream sources | Refused as `CorruptionError`; no allocation near the declared length is made at the source. A path source and a stream with no cheap size are both bounded |
| A backend that needs only the path (a directory, `unrar` over a path), with or without detection | No handle on the source stays open after detection returns. Detection opens and closes its own handle from the path, and the `ArchiveSource`'s handle opens only when a backend reads from it |
