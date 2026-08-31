## ADDED Requirements

### Requirement: An exhaustive prefix scan is available and off by default

Detection's forward scan is bounded by `SFX_MAX` and gated on a prefix cue, because reading
that much from every source a caller opens is not free. A caller who knows better — someone
holding a firmware image, a disk image, or a file with an unrecognised wrapper — SHALL be
able to ask for an unbounded scan.

The opt-in SHALL be a `DetectionBudget` whose `max_scan_bytes` exceeds `SFX_MAX`, passed to
`detect_format(..., budget=)` and threaded through `open_archive` the same way. It SHALL
NOT be an `ArchiveyConfig` field: `#273` already gave detection a cost-control channel, and
a second flag would be two knobs for one decision. A source already matched at offset 0
never consults the scan bound, which is not an error.

The default `BALANCED` budget SHALL leave the scan at `SFX_MAX`. When a larger bound is
set, detection SHALL search that far for the same validated container signatures the cued
scan uses — the opt-in changes *how far* detection looks, never *how much evidence* it
demands. A hit found only this way SHALL report `prefix_kind = UNKNOWN` and
`detected_by = "exhaustive_scan"`.

Because the cost is unbounded in the size of the source, the system SHALL NOT enable this
implicitly — not as a retry after `FormatDetectionError`, and not because an extension
suggested a format that was not found.

#### Scenario: exhaustive scan matrix

| Case | Expected |
| --- | --- |
| Archive magic beyond `SFX_MAX`, default budget | `FormatDetectionError`; the source is not read past the window |
| Same source, budget with `max_scan_bytes` past the window | Detected, `payload_offset` at the payload, `prefix_kind == UNKNOWN` |
| Larger bound, magic present but validation fails | No claim; the scan continues and then fails normally |
| Larger bound, plain archive at offset 0 | Found at tier 1 as usual; no scan performed |
| `FormatDetectionError` under `BALANCED` | SHALL NOT silently retry with a larger scan bound |
