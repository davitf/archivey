## MODIFIED Requirements

### Requirement: Declaring access mode at open_archive()

`open_archive(..., streaming: bool = False)` SHALL accept exactly two modes:

| Mode | Meaning |
| --- | --- |
| `streaming=False` (default) | **Random access.** Load indexes when available. Fail fast at open if the source is non-seekable and the format cannot adapt — never silently degrade to forward-only. Seek points for single-stream formats are built **lazily** on first `seek()`. |
| `streaming=True` | **Forward-only, single pass.** Disable index loading where possible; works on non-seekable sources. Random-access / full-materialization APIs disabled **uniformly** (independent of any loaded index). `members_report_if_available()` stays callable (never scans). |

Non-seekable sources are never given random access: with `streaming=False` the
library fails fast at open when the format needs seek (it does not buffer the
source into memory or a temp file). `streaming=True` is the fix for pipes and
sockets **only where the backend reads front to back** (TAR, the single-file
compressors). A format that needs seek in either mode (ZIP, ISO, 7z, RAR) SHALL be
refused with one message naming a seekable source as the fix, in both modes, rather
than proposing a `streaming=True` retry the same call would then refuse.
Eager seek-point building is not exposed.

A **seekable** stream source is wrapped in a fixed-size read buffer at the source
boundary so `read(n)` returns the full count (`ensure_full_count_reads`) — a raw
`read(n)` may legally return short, and header parsers, archivey's and the stdlib's
alike, read a short return as EOF. That is bounded readahead over a source the caller
already made seekable, not the materialization forbidden above: it never converts a
non-seekable source, and never copies the archive into memory or a temp file. A path
source has always paid the same cost through `open()`'s `BufferedReader`.

#### Scenario: open mode matrix

| Case | Expected |
| --- | --- |
| `streaming=False` on indexed ZIP | Central directory loaded; random access available |
| `streaming=True` on `.tar.gz` | No full-archive index scan; members as stream is read |
| `streaming=False` on non-seekable source, backend reads front to back | Error at open (before member data) naming `streaming=True` — library does not buffer |
| Either mode on non-seekable source, backend needs seek | Same error and same message in both modes, naming a seekable source (buffer to disk or a `BytesIO`) — library does not buffer |
| Seekable stream source, either mode | Buffered at the source boundary for full-count `read(n)`; bounded readahead only — never materialized to memory or disk |
