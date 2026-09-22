# access-mode-and-cost — borrowed source streams delta

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

**Every** stream source SHALL be made full-count at the source boundary
(`ensure_full_count_reads`): a raw `read(n)` may legally return short, and some header
parsers, archivey's and the stdlib's alike, issue one `read(n)` and raise or treat a
short as EOF. The source kinds get that guarantee by different means, and the
difference is read-ahead:

| Source | Boundary wrapper | Read-ahead |
| --- | --- | --- |
| Seekable stream | Fixed-size read buffer (`io.BufferedReader`) | Bounded. Recoverable by seeking, and it collapses the parsers' many tiny reads |
| Non-seekable stream, not already a CPython buffer | `FullCountStream` — gathers by re-asking for the bytes still missing | **None at the boundary.** A `read(n)` on the returned stream takes exactly `n` from the source. Codec layers above it may still buffer |
| Non-seekable stream that is already `io.BufferedReader` / `io.BufferedRandom` | `BorrowedStream` — ownership only (see below) | The caller's buffer already supplies full-count, and keeps reading for itself. Its read-ahead is the caller's; archivey adds no second buffer, and `fileno()` still forwards |

Neither is the materialization forbidden above. `FullCountStream` SHALL hold no
buffered bytes and SHALL report `seekable()` as `False`, so it converts nothing: a
non-seekable source stays non-seekable and `streaming=False` over it still fails fast at
open. A path source has always paid the seekable cost through `open()`'s
`BufferedReader`.

The boundary SHALL also be where ownership of a caller's stream is settled, and what it
returns SHALL NOT be the caller's own object. A source needing neither wrapper above — a
`BytesIO`, an `open()` handle — SHALL be wrapped in `BorrowedStream`, which forwards
reads, seeks, `name`, `fileno` and the cheap size probe, and closes nothing. This is how
"archivey never closes a caller-supplied `BinaryIO`" (`archive-reading`) is kept
regardless of what a backend wraps the source in afterwards: the borrow defaults of the
individual wrappers govern those wrappers, and a caller's object passed through unwrapped
is outside them.

#### Scenario: open mode matrix

| Case | Expected |
| --- | --- |
| `streaming=False` on indexed ZIP | Central directory loaded; random access available |
| `streaming=True` on `.tar.gz` | No full-archive index scan; members as stream is read |
| `streaming=False` on non-seekable source, backend reads front to back | Error at open (before member data) naming `streaming=True` — library does not buffer |
| Either mode on non-seekable source, backend needs seek | Same error and same message in both modes, naming a seekable source (buffer to disk or a `BytesIO`) — library does not buffer |
| Seekable stream source, either mode | Buffered at the source boundary for full-count `read(n)`; bounded readahead only — never materialized to memory or disk |
| Non-seekable stream source, `streaming=True` | The stream the source boundary returns gives full-count `read(n)` with **zero** read-ahead of its own: `seekable()` stays `False`, and a `read(n)` on *that stream* takes exactly `n` bytes from the source. Codec layers above the boundary may still buffer — `DecompressorStream` wraps its input in a `BufferedReader`, so an end-to-end `read(20)` on a compressed non-seekable open takes `io.DEFAULT_BUFFER_SIZE` from the source (8 KiB through 3.13, 128 KiB from 3.14) |
| Non-seekable stream that is already `io.BufferedReader` | Nothing is stacked in front of it; the caller's buffer already supplies full-count. `fileno()` stays intact through the borrow wrapper |
| Non-seekable stream source, metadata probes | The boundary wrapper is transparent: a source carrying `name` / `size` still answers `source_name` and `source_byte_size` through it, so `compressed_source_size` and `ResolvedSource.archive_name` do not degrade. `tell()` does not become available — it raises, as the seek-required refusals depend on |
| Non-seekable short-returning source, any supported streaming format, with and without `format=` | Opens, lists, and reads identically to the full-count source — the guarantee does not depend on detection having run or on a third-party reader's internal buffering |
| Any stream source, every format, measurement on or off | The reader closing does not close the caller's stream, and the caller can still read from it. Holds for a failed open too: the backend releases what it opened, which never includes the caller's object |
