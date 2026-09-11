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
(`ensure_full_count_reads`): a raw `read(n)` may legally return short, and header
parsers, archivey's and the stdlib's alike, read a short return as EOF. The two source
kinds get that guarantee by different means, and the difference is read-ahead:

| Source | Boundary wrapper | Read-ahead |
| --- | --- | --- |
| Seekable stream | Fixed-size read buffer (`io.BufferedReader`) | Bounded. Recoverable by seeking, and it collapses the parsers' many tiny reads |
| Non-seekable stream | `FullCountStream` — gathers by re-asking for the bytes still missing | **None.** Consumes exactly what the caller asked for |

Neither is the materialization forbidden above. `FullCountStream` SHALL hold no
buffered bytes and SHALL report `seekable()` as `False`, so it converts nothing: a
non-seekable source stays non-seekable and `streaming=False` over it still fails fast at
open. A path source has always paid the seekable cost through `open()`'s
`BufferedReader`.

#### Scenario: open mode matrix

| Case | Expected |
| --- | --- |
| `streaming=False` on indexed ZIP | Central directory loaded; random access available |
| `streaming=True` on `.tar.gz` | No full-archive index scan; members as stream is read |
| `streaming=False` on non-seekable source, backend reads front to back | Error at open (before member data) naming `streaming=True` — library does not buffer |
| Either mode on non-seekable source, backend needs seek | Same error and same message in both modes, naming a seekable source (buffer to disk or a `BytesIO`) — library does not buffer |
| Seekable stream source, either mode | Buffered at the source boundary for full-count `read(n)`; bounded readahead only — never materialized to memory or disk |
| Non-seekable stream source, `streaming=True` | Full-count `read(n)` at the boundary with **zero** read-ahead; `seekable()` stays `False`; bytes consumed from the source equal bytes requested |
| Non-seekable short-returning source, any supported streaming format, with and without `format=` | Opens, lists, and reads identically to the full-count source — the guarantee does not depend on detection having run or on a third-party reader's internal buffering |
