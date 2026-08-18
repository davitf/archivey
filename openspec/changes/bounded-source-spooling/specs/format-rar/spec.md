# format-rar — materialization becomes bounded and reported

## MODIFIED Requirements

### Requirement: RAR member payloads are read through RARLAB unrar

RAR member payload bytes SHALL be read through RARLAB `unrar` (ADR 0002), which accepts a
filesystem path and not an in-process stream. When the source is not already a path, the
system SHALL materialize it — and that materialization SHALL be subject to the source spool
limit in `access-mode-and-cost` and SHALL be recorded in `CostReceipt.notes`. It is not an
implementation detail and SHALL NOT be exempt from the limit on the grounds that the source
was already seekable.

The system SHALL NOT materialize a source whose members can all be read directly. A stored,
unencrypted, non-split member is read from the source in place; materialization happens on
the first member that cannot be, and once per reader. Any member of a **solid** archive
requires it, because the payloads are demultiplexed from a single `unrar` pipe.

Multi-volume stream sources SHALL be materialized as a set under the same limit, measured
across all volumes rather than per volume.

#### Scenario: RAR materialization matrix

| Case | Expected |
| --- | --- |
| Path source, any member | No spool; `unrar` is given the caller's path |
| Stream source, listing only | No spool; RAR metadata is parsed natively |
| Stream source, stored unencrypted member | No spool; the member is read directly from the source |
| Stream source, compressed member, within the limit | One spool of the whole archive, recorded in `CostReceipt.notes` |
| Stream source, solid archive, any member | One spool; the pipe demux requires it |
| Stream source, second compressed member after the first | No second spool; materialization is once per reader |
| Stream source, archive larger than the limit | `ResourceLimitError` before writing, since the size is known |
| Stream source, spooling set to none, compressed member | Typed refusal; no bytes written |
| Multi-volume stream source | One spool set; the limit applies to the total across volumes |
| Reader closed | Temporary file or directory removed |
