## MODIFIED Requirements

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

**Backends SHALL report the origin they resolved.** Whether the offset arrived from
detection or was discovered by the backend’s own start-offset rules, the reader SHALL
surface it as `ArchiveInfo.prefix_kind` / `ArchiveInfo.payload_offset`
(`archive-data-model`). A backend that discovers the origin and discards it is
non-conforming: the two open paths SHALL NOT differ in what the caller can learn about
where the archive began.

Where the backend opened a prefixed archive without establishing the origin, it SHALL
report `payload_offset is None` rather than `0`. Backends whose formats cannot carry a
prefix SHALL report `NONE` / `0` and are unaffected.

Because the reader carries the origin, `open_archive` callers SHALL NOT need a second
`detect_format()` call to learn it, on either open path — including on non-seekable
sources, where re-detecting is not available at all.

#### Scenario: payload_offset hand-off

| Case | Expected |
| --- | --- |
| Auto-detect SFX RAR/7z/ZIP with `payload_offset == N` | Backend opens via start-offset arg or offset view; real members listed |
| `payload_offset == 0` | Unchanged open-at-current-position behaviour |
| Explicit `format=` | Detection skipped; backend SFX/start-offset rules apply |
| Bare seek only (no start-offset / no offset view) | Insufficient for 7z; MUST NOT be the sole hand-off mechanism |

#### Scenario: the two open paths report the same origin

| Case | Expected |
| --- | --- |
| SFX 7z opened auto-detected vs `format=SEVEN_Z` | Same `info.prefix_kind` and `info.payload_offset` |
| SFX RAR opened auto-detected vs `format=RAR` | Same `info.prefix_kind` and `info.payload_offset` |
| Forced-format open on a prefixed archive | Origin reported from the backend’s own resolution, not `None` merely because detection was skipped |
| Backend cannot establish the origin | `payload_offset is None`; never a fabricated `0` |
| Reading the origin | No second `detect_format()` call required |
