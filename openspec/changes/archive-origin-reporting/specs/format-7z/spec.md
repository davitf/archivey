## MODIFIED Requirements

### Requirement: Accept a non-zero archive start offset (SFX)

The 7z reader SHALL accept an archive whose signature header (`7z\xBC\xAF'\x1C`)
begins at a non-zero byte offset — whether supplied as an explicit start offset
from detection (`payload_offset`) or discovered by a bounded forward scan when
magic is absent at the open position (forced `format=SEVEN_Z` on an SFX stub).

All absolute seeks derived from the signature header SHALL be relative to that
signature origin. The system SHALL read in place and SHALL NOT copy the archive
to a temporary file solely to strip a stub. The forced-format scan bound SHALL be
the shared `SFX_MAX` constant (same binding as the RAR parser and
`detect_format`; today 2 MiB).

Origin resolution SHALL use the shared resolver in `internal/sfx.py` rather than a
7z-private copy: a fast-path read at the open position, a bounded forward scan on a miss,
and `CorruptionError` past the bound. When detection supplied a correct `payload_offset`
the fast path SHALL hit, so the scan does not run on the detected path.

The reader SHALL report the resolved origin as `ArchiveInfo.prefix_kind` /
`ArchiveInfo.payload_offset` (`archive-data-model`), on both the detected and the
forced-format path.

#### Scenario: 7z SFX / start-offset matrix

| Case | Expected |
| --- | --- |
| Magic at open origin (offset 0) | Unchanged success path |
| Detection supplies `payload_offset == N` | Opens at N; no forward scan performed |
| Forced `format=SEVEN_Z`, magic at N within `SFX_MAX` | Bounded scan finds N; members listed |
| Forced `format=SEVEN_Z`, no magic within `SFX_MAX` | `CorruptionError`, not an empty archive |
| Either path, magic at N | `info.payload_offset == N` and `info.prefix_kind` is not `NONE` |
