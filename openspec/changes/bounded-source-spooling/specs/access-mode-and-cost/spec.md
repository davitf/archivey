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

The system SHALL NOT expose *why* a spool was needed as a configuration axis. Materializing
a seekable source for an external binary that accepts only a filesystem path, and
materializing a non-seekable source so a seek-requiring format can read it, are the same
operation at the same cost with the same remedy, and SHALL be governed by the same limit.

A spool that would exceed the limit SHALL raise `ResourceLimitError` — the same family as
`ExtractionLimits` and `ListingLimits` — and SHALL do so **before writing any bytes** when
the source's size is known in advance. When the size is not known, the system SHALL enforce
the limit during the write and SHALL remove the partial temporary file before raising.

The system SHALL allow the caller to name the directory used for spooling.

#### Scenario: spool limit matrix

| Case | Expected |
| --- | --- |
| Limit is a byte count, source smaller | Spooled; `CostReceipt.notes` records the spool and its size |
| Limit is a byte count, source larger, size known in advance | `ResourceLimitError` before any bytes are written |
| Limit is a byte count, source larger, size not known in advance | `ResourceLimitError` during the write; partial file removed |
| Limit is unlimited | Spooled whatever the size; still recorded in `CostReceipt.notes` |
| Limit is none, operation needs a spool | Refused with a typed error; nothing is written |
| Limit is none, operation needs no spool | Unaffected |
| Caller names a spool directory | That directory is used; the platform default is not consulted |
| Reader closed | Temporary file or directory removed |

### Requirement: Every spool is reported in the cost receipt

The system SHALL record each spool it performs in `CostReceipt.notes`, naming the byte
count. The system SHALL NOT emit a diagnostic for a spool: the `diagnostics` admission
clause covers what the caller could not determine from the declared contract of the call,
and a spool inside a limit the caller configured is declared; the placement clause prefers a
structured field where one exists, and `CostReceipt.notes` is that field.

#### Scenario: spool reporting matrix

| Case | Expected |
| --- | --- |
| A spool occurs | `CostReceipt.notes` gains an entry naming the byte count |
| A spool occurs | No diagnostic is emitted for the spool itself |
| No spool occurs | `CostReceipt.notes` is unchanged by spooling |
| Two operations on one reader both need the source materialized | One spool, one note; materialization happens once per reader |

### Requirement: Spooling happens at the operation that needs it, and the timing is stated

The system SHALL spool at the first operation that requires it, not eagerly at open. Which
operation that is depends on the source and the format, and the difference is
caller-visible: an archive whose metadata is parsed natively can be **listed** without
paying for a spool, while a source that must be made seekable before the format can be
opened at all pays at open. The documentation SHALL state when each happens rather than
leaving the caller to infer it, and `CostReceipt.notes` makes the timing observable.

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

## MODIFIED Requirements

### Requirement: Fail fast on non-seekable random access

With `streaming=False`, when the format requires a seekable source and the source is not
seekable, the system SHALL raise at open rather than buffering the source implicitly. The
system SHALL NOT silently buffer a non-seekable source into memory or temporary storage to
make a seek-requiring format work.

**This rule is unchanged in substance and now names its one exception:** a spool that the
configured limit permits is not *implicit* buffering. ADR 0010 forbids hidden unbounded
resource use, not temporary storage as such; a spool bounded by an explicit limit and
reported in `CostReceipt.notes` satisfies the decision's reasoning rather than defeating it.
With spooling set to none, the behaviour is exactly as before.

When the system raises for this reason, the error message SHALL name the setting that would
permit the spool, so a caller who wants it can find it from the failure.

#### Scenario: non-seekable random access matrix

| Case | Expected |
| --- | --- |
| Pipe source, ZIP / 7z / RAR / ISO, spooling set to none, `streaming=False` | `StreamNotSeekableError` at open, naming the setting |
| Pipe source, ZIP / 7z / RAR / ISO, spooling set to none, `streaming=True` | `StreamNotSeekableError` at open, naming the setting |
| Pipe source, TAR or single-file compressor, `streaming=True` | Opens and streams forward; no spool involved |
| Pipe source, seek-requiring format, spool within the limit | Opens; `CostReceipt.notes` records the spool |
| Seekable source, any format | Unchanged; no spool is considered for seekability |
