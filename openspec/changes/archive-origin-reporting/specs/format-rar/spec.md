## ADDED Requirements

### Requirement: Accept a non-zero archive start offset (SFX)

The RAR reader SHALL accept an archive whose marker (`Rar!\x1a\x07\x00` for RAR4 or
`Rar!\x1a\x07\x01\x00` for RAR5) begins at a non-zero byte offset — whether supplied as an
explicit start offset from detection (`payload_offset`) or discovered by a bounded forward
scan when the marker is absent at the open position (forced `format=RAR` on an SFX stub).

This is the same contract `format-7z` states for its signature header, and SHALL be
implemented through the same shared resolver in `internal/sfx.py`: a fast-path read at the
open position, a bounded forward scan on a miss, and `CorruptionError` past the bound. The
scan bound SHALL be the shared `SFX_MAX` constant.

Resolution SHALL yield the RAR **version** alongside the offset, from whichever marker
matched, so version determination is not a second search. Scanning for both markers rather
than their shared `Rar!\x1a\x07` prefix is what makes this exact: a stub containing the
bare prefix without a valid version byte SHALL NOT be accepted as the payload.

Member and header offsets SHALL be relative to the resolved origin. The system SHALL read
in place and SHALL NOT copy the archive to a temporary file solely to strip a stub; a
backend that hands the source to an external tool MAY pass the original path where that
tool locates the payload itself.

**The resolved origin SHALL be the sum of the supplied start offset and the offset the
resolver found within it**, as `format-7z` already computes it. Reporting either component
alone makes the two doors disagree: on the auto-detect path the supplied offset carries the
whole value and the resolver returns 0, while on the forced path the supplied offset is 0
and the resolver returns the whole value. `ArchiveInfo.payload_offset` SHALL be that sum,
measured from the start of `source` (`archive-data-model`).

A non-zero start offset SHALL NOT be combined with a multi-volume set: the offset describes
one file and the volumes are separate ones. The reader SHALL raise
`UnsupportedFeatureError` rather than applying the offset to a volume it does not describe.
**This check SHALL run after origin resolution**, against the resolved origin rather than
the supplied start offset — otherwise a forced-format open of a self-extracting first
volume passes the guard with a start offset of 0 and reaches the case the requirement
exists to reject.

The reader SHALL report the resolved origin as `ArchiveInfo.prefix_kind` /
`ArchiveInfo.payload_offset` (`archive-data-model`), on both the detected and the
forced-format path.

#### Scenario: RAR SFX / start-offset matrix

| Case | Expected |
| --- | --- |
| Marker at open origin (offset 0) | Unchanged success path; version from the marker read |
| Detection supplies `payload_offset == N` | Opens at N; no forward scan performed |
| Forced `format=RAR`, RAR5 marker at N within `SFX_MAX` | Bounded scan finds N; version 5; members listed |
| Forced `format=RAR`, RAR4 marker at N within `SFX_MAX` | Bounded scan finds N; version 4; members listed |
| Stub contains bare `Rar!\x1a\x07` without a valid version byte, real marker later | Resolves to the real marker, not the decoy |
| Forced `format=RAR`, no marker within `SFX_MAX` | `CorruptionError`, not an empty archive |
| Non-zero start offset with a multi-volume set | `UnsupportedFeatureError` |
| Forced `format=RAR` on a self-extracting first volume of a multi-volume set | `UnsupportedFeatureError` — the guard sees the resolved origin, not the supplied `0` |
| Either path, marker at N | `info.payload_offset == N`, measured from the start of `source` |
