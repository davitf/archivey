## ADDED Requirements

### Requirement: Honour detection payload_offset at open

When `detect_format` returns `payload_offset > 0`, `open_archive` (auto-detect
path) SHALL open the archive at that byte offset by either (a) passing an
explicit start-offset argument into `backend.open_read`, or (b) handing the
backend a bounded offset view / slice whose byte 0 *is* the payload. A bare
`seek` on a shared handle is **not** sufficient: backends that perform absolute
seeks (notably 7z’s `read_signature_and_next_header`, which `seek(0)`s then
seeks to `_SIGNATURE_HEADER_SIZE + next_header_offset`) discard caller
positioning. The system SHALL NOT copy the remainder of the source to a
temporary file solely to strip an SFX stub.

An explicit `format=` that bypasses detection retains each backend’s own
start-offset / SFX rules (`format-rar`, `format-7z`).

#### Scenario: payload_offset hand-off

| Case | Expected |
| --- | --- |
| Auto-detect SFX RAR/7z with `payload_offset == N` | Backend opens via start-offset arg or offset view; real members listed |
| `payload_offset == 0` | Unchanged open-at-current-position behaviour |
| Explicit `format=` | Detection skipped; backend SFX/start-offset rules apply |
| Bare seek only (no start-offset / no offset view) | Insufficient for 7z; MUST NOT be the sole hand-off mechanism |
