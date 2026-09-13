## ADDED Requirements

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

#### Scenario: 7z SFX / start-offset matrix

| Case | Expected |
| --- | --- |
| Magic at open origin (offset 0) | Unchanged success path |
| Explicit start offset N with magic at N | Signature parsed at N; members listed |
| Forced `format=SEVEN_Z`, `MZ` stub, magic at N within `SFX_MAX` | Scan finds magic; open succeeds |
| Forced `format=SEVEN_Z`, no magic within `SFX_MAX` | `CorruptionError` (not a silent empty archive) |
| Packed streams after an SFX signature | Pack/header seeks use signature origin; members readable |
