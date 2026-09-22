# access-mode-and-cost — bounded source spooling delta

## ADDED Requirements

### Requirement: Source spooling is bounded by a single configured limit

The system SHALL write an archive source to temporary storage only when an operation
requires it, and SHALL bound every such spool by one configured limit. The limit SHALL
accept three settings:

- a **byte count** — spool when needed, up to that many bytes;
- an **unlimited** sentinel, matching the `ExtractionLimits.UNLIMITED` /
  `ListingLimits.UNLIMITED` pattern — never refuse on size;
- **none** — never spool.

The default SHALL be **1 GiB**. A byte count is the default rather than unlimited or none:
unlimited leaves the behaviour that makes this a defect in place, and none removes a
capability that works today. 1 GiB keeps essentially every archive that works today working
while making the bound real.

The system SHALL NOT expose *why* a spool was needed as a configuration axis. Materializing
a seekable source for an external binary that accepts only a filesystem path, and
materializing a non-seekable source so a seek-requiring format can read it, are the same
operation at the same cost with the same remedy, and SHALL be governed by the same limit.

A spool that would exceed the limit SHALL raise `SpoolLimitExceededError`, and SHALL do so
**before writing any bytes** when the source's size is known in advance. When the size is
not known, the system SHALL enforce the limit during the write and SHALL remove the partial
temporary file before raising.

`SpoolLimitExceededError` SHALL subclass `ResourceLimitError`, so that `except
ResourceLimitError` keeps catching every configured-limit trip while a caller who wants to
distinguish the spool can. The **none** setting SHALL raise the same error: a limit of none
is a limit of zero bytes, and any spool exceeds it. The system SHALL NOT use
`UnsupportedOperationError` — a limit the caller can raise is not something that cannot be
done — nor `ArchiveyUsageError`, which sits outside `ArchiveyError` (ADR 0012) and would
present a configured bound as a programming mistake.

The system SHALL allow the caller to name the directory used for spooling.

#### Scenario: spool limit matrix

| Case | Expected |
| --- | --- |
| Limit is a byte count, source smaller | Spooled; the open-time caveat already named the bound |
| Limit is a byte count, source larger, size known in advance | `SpoolLimitExceededError` before any bytes are written |
| Limit is a byte count, source larger, size not known in advance | `SpoolLimitExceededError` during the write; partial file removed |
| Limit is unlimited | Spooled whatever the size; still recorded in `CostReceipt.notes` |
| Limit is none, operation needs a spool | `SpoolLimitExceededError`; nothing is written |
| No limit configured | 1 GiB applies |
| Caller catches `ResourceLimitError` | The spool refusal is caught, like a listing or extraction limit |
| Limit is none, operation needs no spool | Unaffected |
| Caller names a spool directory | That directory is used; the platform default is not consulted |
| Reader closed | Temporary file or directory removed |

### Requirement: The spool caveat is stated at open and names its bound

Where a reader may spool its source, `CostReceipt.notes` SHALL carry the caveat **at
open**, and that caveat SHALL name the configured limit that bounds the copy. A caller
therefore learns the worst case before the first read rather than the actual cost after
it.

`CostReceipt` is an immutable open-time cost description, and the caveat is a static
open-time statement, not an occurrence log: it SHALL be present even if nothing is
ultimately spooled, and SHALL NOT be added later by a spool that happens after open.
That contract is already stated for RAR in `format-rar` and is unchanged here; what this
change adds is the bound in the caveat's text.

The system SHALL NOT emit a diagnostic for a spool. The `diagnostics` admission clause
covers what the caller could not determine from the declared contract of the call, and a
spool inside a limit the caller configured, announced at open, is declared twice over.

#### Scenario: spool reporting matrix

| Case | Expected |
| --- | --- |
| Reader that may spool, at open | `CostReceipt.notes` carries the caveat, naming the limit |
| Only stored members are read, so nothing is spooled | The caveat is still present — it is a statement about the reader, not a log |
| A spool occurs after open | `CostReceipt.notes` is unchanged; it was already accurate |
| Reader that cannot spool (path source, or limit set to none) | No caveat |
| Any spool | No diagnostic is emitted for it |

### Requirement: Spooling happens at the operation that needs it, and the timing is stated

The system SHALL spool at the first operation that requires it, not eagerly at open. Which
operation that is depends on the source and the format, and the difference is
caller-visible: an archive whose metadata is parsed natively can be **listed** without
paying for a spool, while a source that must be made seekable before the format can be
opened at all pays at open. The documentation SHALL state when each happens rather than
leaving the caller to infer it. The open-time caveat does not distinguish the two, because
it describes the worst case rather than what occurred; the documentation is where a caller
learns which operation pays.

#### Scenario: spool timing matrix

| Case | Expected |
| --- | --- |
| Seekable stream, native metadata parse, listing only | No spool; listing does not materialize the source |
| Seekable stream, first member requiring an external binary | Spool at that read, not at open |
| Non-seekable source, seek-requiring format, spooling permitted | Spool at open, because the format cannot be opened otherwise |
| Non-seekable source, forward-only format | No spool; the format streams |

### Requirement: Free-space pre-flight is best-effort and never a guarantee

Where the system checks available space before spooling, it SHALL treat the result as a
fast-fail heuristic only, and SHALL NOT present it as a guarantee that the spool will
succeed. The **byte limit is the honest guard**; the space check exists to fail earlier and
more legibly than a filesystem error would.

The system SHALL NOT assume the spool directory is on the filesystem a caller expects: the
platform temporary directory may be a different device than the working directory, may be
sized independently of it, and **may be memory-backed** — in which case a spool nominally
"to disk" consumes RAM, which is the unbounded memory use ADR 0010 exists to prevent. The
documentation SHALL state that a memory-backed temporary directory makes the byte limit a
memory limit.

#### Scenario: pre-flight and directory matrix

| Case | Expected |
| --- | --- |
| Free space reported below the needed size | Fail before writing, naming the shortfall |
| Free space reported sufficient, filesystem fills mid-write | The underlying `OSError` propagates translated; the pre-flight is not claimed to have promised otherwise |
| Source size not known in advance | No pre-flight is possible; the limit is enforced during the write |
| Spool directory is memory-backed | Behaviour unchanged and the limit still applies; the documentation says what that means |

### Requirement: A spooled source does not turn a streaming read into a random-access one

With `streaming=True`, when the source has been spooled, the system SHALL read forward from
the spooled file and SHALL NOT switch to random access because spooling made the source
seekable. `streaming=True` states an intent about how the archive is read; spooling is an
implementation detail of reading the caller's source, not a revision of that intent.

The system SHALL apply this uniformly across formats. **If `streaming=True` means something
different per format, code that is wrong passes its tests on the format its author happened
to use and fails in production when another appears** — which is the same reasoning behind
disabling random-access APIs independently of any loaded index.

#### Scenario: streaming over a spooled source matrix

| Case | Expected |
| --- | --- |
| `streaming=True`, source spooled | Forward reads from the spooled file; random-access APIs stay disabled |
| `streaming=True`, source spooled, caller requests random access | Refused as it is for any `streaming=True` reader |
| `streaming=False`, source spooled | Random access as usual |
| `streaming=True`, no spool needed | Unchanged |

## MODIFIED Requirements

### Requirement: Declaring access mode at open_archive()

`open_archive(..., streaming: bool = False)` SHALL accept exactly two modes:

| Mode | Meaning |
| --- | --- |
| `streaming=False` (default) | **Random access.** Load indexes when available. Fail fast at open if the source is non-seekable and the format cannot adapt and the configured spool limit does not permit materializing it — never silently degrade to forward-only. Seek points for single-stream formats are built **lazily** on first `seek()`. |
| `streaming=True` | **Forward-only, single pass.** Disable index loading where possible; works on non-seekable sources **where the backend reads front to back** (see below). Random-access / full-materialization APIs disabled **uniformly** (independent of any loaded index). `members_report_if_available()` stays callable (never scans). |

Non-seekable sources are never given random access *implicitly*: with
`streaming=False` the library fails fast at open when the format needs seek, and
SHALL NOT buffer the source into memory or a temp file on its own initiative.
`streaming=True` is the fix for pipes and sockets **only where the backend reads
front to back** (TAR, the single-file compressors). A format that needs seek in
either mode (ZIP, ISO, 7z, RAR) SHALL be refused with one message naming a seekable
source as the fix, in both modes, rather than proposing a `streaming=True` retry the
same call would then refuse.

**The one exception is a spool the caller configured.** When the spool limit permits
it, the system MAY materialize a non-seekable source to temporary storage so a
seek-requiring format can be opened, bounded and reported as `access-mode-and-cost`
requires. That is not implicit buffering: the caller set the bound, and every spool
appears in `CostReceipt.notes`. With the limit set to none the behaviour is exactly
as stated above, and the refusal message SHALL name the setting that would permit
the spool, so the error teaches the fix.
Eager seek-point building is not exposed.

**Every** stream source SHALL be made full-count at the source boundary
(`ensure_full_count_reads`): a raw `read(n)` may legally return short, and some header
parsers, archivey's and the stdlib's alike, issue one `read(n)` and raise or treat a
short as EOF. The source kinds get that guarantee by different means, and the
difference is read-ahead:

| Source | Boundary wrapper | Read-ahead |
| --- | --- | --- |
| Seekable stream, not already buffered | Fixed-size read buffer (`io.BufferedReader`) | Bounded. Recoverable by seeking, and it collapses the parsers' many tiny reads |
| Seekable stream that is already buffered (a `BytesIO`, an `open()` handle) | `BorrowedStream` — ownership only (see below) | **None added by the boundary.** The source is already full-count, so nothing is stacked in front of it; `fileno()` still forwards |
| Non-seekable stream, not already a CPython buffer | `FullCountStream` — gathers by re-asking for the bytes still missing | **None at the boundary.** A `read(n)` on the returned stream takes exactly `n` from the source. Codec layers above it may still buffer |
| Non-seekable stream that is already `io.BufferedReader` / `io.BufferedRandom` | `BorrowedStream` — ownership only (see below) | The caller's buffer already supplies full-count, and keeps reading for itself. Its read-ahead is the caller's; archivey adds no second buffer, and `fileno()` still forwards |

Neither is the materialization discussed above. `FullCountStream` SHALL hold no
buffered bytes and SHALL report `seekable()` as `False`, so it converts nothing: a
non-seekable source stays non-seekable, and `streaming=False` over it still fails fast
at open unless a configured spool makes it seekable first. A path source has always paid the seekable cost through `open()`'s
`BufferedReader`.

The boundary SHALL also be where ownership of a caller's stream is settled, and what it
returns SHALL NOT be the caller's own object. A source that needs neither the buffer nor
the gatherer — a `BytesIO`, an `open()` handle — SHALL still be wrapped, in
`BorrowedStream`, which forwards reads, seeks, `name`, `fileno` and the cheap size
probe, and closes nothing. This is how
"archivey never closes a caller-supplied `BinaryIO`" (`archive-reading`) is kept
regardless of what a backend wraps the source in afterwards: the borrow defaults of the
individual wrappers govern those wrappers, and a caller's object passed through unwrapped
is outside them.

#### Scenario: open mode matrix

| Case | Expected |
| --- | --- |
| `streaming=False` on indexed ZIP | Central directory loaded; random access available |
| `streaming=True` on `.tar.gz` | No full-archive index scan; members as stream is read |
| `streaming=False` on non-seekable source, backend reads front to back | Error at open (before member data) naming `streaming=True` — library does not buffer on its own initiative |
| Either mode on non-seekable source, backend needs seek, spooling set to none | Same error and same message in both modes, naming a seekable source (buffer to disk or a `BytesIO`) and the setting that would permit a spool |
| Either mode on non-seekable source, backend needs seek, spool within the limit | Opens; the source is materialized at open and the spool is in `CostReceipt.notes` |
| Non-seekable source, backend needs seek, archive over the spool limit | `SpoolLimitExceededError` |
| Seekable stream source, either mode | Full-count `read(n)` at the source boundary, by whichever of the two the source needs: one that is not already buffered gets a fixed-size `io.BufferedReader` (bounded readahead only), and one that already is (a `BytesIO`, an `open()` handle) is borrowed as it stands, with no readahead added. Never materialized to memory or disk |
| Non-seekable stream source, `streaming=True` | The stream the source boundary returns gives full-count `read(n)` with **zero** read-ahead of its own: `seekable()` stays `False`, and a `read(n)` on *that stream* takes exactly `n` bytes from the source. Codec layers above the boundary may still buffer — `DecompressorStream` wraps its input in a `BufferedReader`, so an end-to-end `read(20)` on a compressed non-seekable open takes `io.DEFAULT_BUFFER_SIZE` from the source (8 KiB through 3.13, 128 KiB from 3.14) |
| Non-seekable stream that is already `io.BufferedReader` | Nothing is stacked in front of it; the caller's buffer already supplies full-count. `fileno()` stays intact through the borrow wrapper |
| Non-seekable stream source, metadata probes | The boundary wrapper forwards what the probes need: a source carrying `name` / `size` still answers `source_name` and `source_byte_size` through it, so `compressed_source_size` and `ResolvedSource.archive_name` do not degrade. It is not transparent in general — what a backend sees is the wrapper's surface, not the source's class, so `peek` / `read1` / `detach` / `BytesIO.getvalue` do not survive it. `tell()` does not become available either — it raises, as the seek-required refusals depend on |
| Non-seekable short-returning source, any supported streaming format, with and without `format=` | Opens, lists, and reads identically to the full-count source — the guarantee does not depend on detection having run or on a third-party reader's internal buffering |
| Any stream source, every format, measurement on or off | The reader closing does not close the caller's stream, and the caller can still read from it. Holds for a failed open too: the backend releases what it opened, which never includes the caller's object |
