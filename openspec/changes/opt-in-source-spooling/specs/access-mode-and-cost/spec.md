# access-mode-and-cost — source spooling delta

## ADDED Requirements

### Requirement: Source spooling is policy-driven, bounded, and reported

The system SHALL treat writing an archive source to temporary storage as a **policy
decision with two kinds**, never as an implementation detail:

- A **tool-tax spool** materializes an already-seekable source so an external binary that
  accepts only a filesystem path can read it. It grants the caller no capability the source
  did not already have.
- A **capability spool** materializes a non-seekable source so a format requiring
  seekability can be opened. It overrides the access mode the caller chose.

The policy SHALL be a frozen value object reachable from `ArchiveyConfig`, carrying at
least: whether each kind is permitted, a **maximum byte count** for a single spool, and an
optional directory in which to spool. Defaults SHALL be tool-tax **permitted** and
capability **not permitted**.

A spool that would exceed the maximum SHALL raise `ResourceLimitError` — the same family as
`ExtractionLimits` and `ListingLimits` — rather than proceeding, and SHALL do so **before**
writing bytes when the source's size is known.

Every spool that occurs SHALL be recorded in `CostReceipt.notes`, naming the kind and the
byte count. A spool SHALL NOT be reported as a diagnostic: the `diagnostics` admission
clause covers what the caller could not determine from the declared contract of the call,
and a spool the caller enabled and bounded is declared. Temporary storage SHALL be removed
when the reader is closed.

#### Scenario: spooling policy matrix

| Case | Expected |
| --- | --- |
| Seekable source, external-binary backend, tool-tax permitted (default) | Source is spooled; `CostReceipt.notes` records kind and size |
| Seekable source, external-binary backend, tool-tax refused by policy | `ArchiveyUsageError` naming the policy field, at the read that needs it |
| Non-seekable source, seek-requiring format, capability not permitted (default) | `StreamNotSeekableError`, message naming the option that would allow it |
| Non-seekable source, seek-requiring format, capability permitted | Source is spooled; `CostReceipt.notes` records kind and size |
| Spool would exceed the configured maximum, size known in advance | `ResourceLimitError` before any bytes are written |
| Spool exceeds the configured maximum mid-write, size not known in advance | `ResourceLimitError`; partial temporary file removed |
| Any spool, reader closed | Temporary file or directory removed |
| Any spool | No diagnostic is emitted for the spool itself |

### Requirement: Free-space pre-flight is best-effort and never a guarantee

Where the system checks available space before spooling, it SHALL treat the result as a
fast-fail heuristic only, and SHALL NOT present it as a guarantee that the spool will
succeed. The **byte limit is the honest guard**; the space check exists to fail earlier and
more legibly than a filesystem error would.

The system SHALL NOT assume the spool directory is on the filesystem a caller expects: the
platform temporary directory may be a different device than the working directory, may be
sized independently of it, and **may be a memory-backed filesystem** — in which case a
spool nominally "to disk" consumes RAM, which is the unbounded memory use ADR 0010 exists
to prevent. Because of this the policy SHALL let the caller name the spool directory, and
the documentation SHALL state that a memory-backed temporary directory makes the byte limit
a memory limit.

#### Scenario: pre-flight and directory selection matrix

| Case | Expected |
| --- | --- |
| Free space reported below the needed size | Fail before writing, with an error naming the shortfall |
| Free space reported sufficient, filesystem fills mid-write | The underlying `OSError` propagates translated; the pre-flight is not claimed to have promised otherwise |
| Caller names a spool directory | That directory is used; the platform default is not consulted |
| Caller names no directory | The platform temporary directory is used |
| Spool directory is memory-backed | Behaviour is unchanged and the limit still applies; the caller is responsible for knowing, and the documentation says so |

## MODIFIED Requirements

### Requirement: Fail fast on non-seekable random access

With `streaming=False`, when the format requires a seekable source and the source is not
seekable, the system SHALL raise at open rather than buffering the source implicitly. The
system SHALL NOT silently buffer a non-seekable source into memory or temporary storage to
make a seek-requiring format work.

**This rule is unchanged in substance and now names its one exception:** a *capability
spool* that the caller has explicitly permitted through the spooling policy is not implicit
buffering, and is allowed. ADR 0010 forbids hidden unbounded resource use, not temporary
storage as such; an opt-in that is bounded by an explicit byte limit and reported in
`CostReceipt.notes` satisfies the decision's reasoning rather than defeating it. With no
such opt-in the behaviour is exactly as before.

When the system raises for this reason, the error message SHALL name the policy option that
would permit the spool, so a caller who wants it can find it from the failure.

#### Scenario: non-seekable random access matrix

| Case | Expected |
| --- | --- |
| Pipe source, ZIP / 7z / RAR / ISO, `streaming=False`, no capability spool | `StreamNotSeekableError` at open, naming the option |
| Pipe source, ZIP / 7z / RAR / ISO, `streaming=True`, no capability spool | `StreamNotSeekableError` at open, naming the option |
| Pipe source, TAR or single-file compressor, `streaming=True` | Opens and streams forward; no spool involved |
| Pipe source, seek-requiring format, capability spool permitted and within the limit | Opens; `CostReceipt.notes` records the spool |
| Seekable source, any format | Unchanged; no capability spool is considered |
