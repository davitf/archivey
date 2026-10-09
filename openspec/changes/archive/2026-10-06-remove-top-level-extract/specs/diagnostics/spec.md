## MODIFIED Requirements

### Requirement: Lifecycle-aware aggregation and attachment

The system SHALL own diagnostic aggregation per lifetime as follows:

| Lifetime | Collector ownership |
| --- | --- |
| Standalone `detect_format` | One collector; final summary on `FormatInfo` |
| `open_archive` + auto-detect | Prospective-reader collector created before detection, passed in, owned by successful reader — no seed/merge/replay/copy. Same counters, retained tuple, ids, order, one-time budget charges |
| Reader-owned stream | Operation-filtered view over the reader collector (no second aggregate retain) |
| Standalone stream | Own stream-lifetime collector |

Attachment rules:

- Natural member-metadata diagnostics MAY attach to `ArchiveMember.diagnostics`
  under the shared budget.
- `ExtractionResult` has **no** diagnostics field, and no per-member extraction
  outcome is emitted as a diagnostic at all: `status`, `error`, `requested_path`,
  `presented_name` and the failure-group fields are the whole record.
- Detection conflict attaches to `FormatInfo`.
- Runtime rewind, seek-index degradation, scan race, archive EOF: aggregate-only —
  never attached to frozen `CostReceipt` or `ArchiveInfo`.

`ExtractionReport.diagnostics` SHALL remain a real summary: reading an archive during
extraction still emits reading diagnostics (invalid timestamps, unresolvable symlinks,
unverifiable digests, rewinds), and those stay in the aggregate. What leaves the
channel is the per-member *extraction outcome*, not everything observed while
extracting.

#### Scenario: lifetime matrix

| Case | Expected |
| --- | --- |
| `open_archive` detects conflict, opens reader | One collector/budget; conflict one aggregate slot; visible on reader summary; no copy |
| `open_archive` then `extract_all()` | Report is the watermark range of that call; detection and open diagnostics stay on `reader.diagnostics` |
| Reader-owned stream rewinds | On stream op snapshot + cumulative reader; `CostReceipt`/`ArchiveInfo` unchanged |
| Extraction hits a member with an invalid timestamp and a blocked member | Timestamp diagnostic in the report summary; the block appears only as a `BLOCKED` result |
