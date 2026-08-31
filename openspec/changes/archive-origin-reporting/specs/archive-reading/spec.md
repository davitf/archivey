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
surface it as `ArchiveInfo.payload_offset` (`archive-data-model`). A backend that discovers
the origin and discards it is non-conforming: the two open paths SHALL NOT differ in what
the caller can learn about **where** the archive began.

`ArchiveInfo.prefix_kind` is a separate question and SHALL NOT be inferred from the offset.
Classifying a prefix requires inspecting it, which only detection does; a forced-`format=`
open SHALL report `prefix_kind is None` (*not established*) rather than guessing a kind from
`payload_offset > 0`. Collapsing every non-zero offset to `EXECUTABLE` would reintroduce the
`is_sfx` conflation this change exists to avoid — a `zipapp`, an executable JAR and a
JPEG-with-appended-ZIP are all non-zero and none is self-extracting.

This SHALL hold for every prefix-capable backend, including on the forced path: 7z and RAR
resolve the origin with their own bounded scan, and ZIP derives it from the central
directory it already parsed (`format-zip`). Where a backend genuinely cannot establish the
origin — an empty ZIP has no local file header to measure from — it SHALL report
`payload_offset is None` and `prefix_kind is None` rather than `0`. Backends whose formats
cannot carry a prefix SHALL report `NONE` / `0` and are unaffected.

A backend SHALL NOT perform additional I/O solely to populate these fields: the origin is
reported where it is already known or falls out of work the open performs anyway, and
reported as unestablished otherwise.

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
| SFX 7z opened auto-detected vs `format=SEVEN_Z` | Same `info.payload_offset`; `prefix_kind` classified on the detected path, `None` on the forced one |
| SFX RAR opened auto-detected vs `format=RAR` | Same `info.payload_offset`; `prefix_kind` as above |
| Forced-format open, non-zero offset | `prefix_kind is None` — never inferred as `EXECUTABLE` from the offset alone |
| Forced-format open on a prefixed archive | Origin reported from the backend’s own resolution, not `None` merely because detection was skipped |
| Forced `format=ZIP` on a prefixed ZIP | `payload_offset == N` from the earliest local header, with no extra read |
| Backend cannot establish the origin (empty ZIP, no members) | `payload_offset is None` and `prefix_kind is None`; never a fabricated `0` |
| Reading the origin | No second `detect_format()` call required |
