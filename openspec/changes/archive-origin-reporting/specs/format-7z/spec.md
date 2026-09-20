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

The forced-format scan SHALL skip a candidate whose signature header does not
validate and continue, returning the earliest VALID match. If none validate, it
SHALL fall back to the earliest identified candidate so a damaged or empty
payload still reaches the parser. After `MAX_VALIDATED_CANDIDATES` (256)
rejected candidates the scan SHALL stop and raise `CorruptionError` naming the
cap. That bound is structural (a real SFX stub does not carry hundreds of
format magics, and the parser has no `DetectionBudget`) and is not a
`ListingLimits` knob. A miss with no candidate SHALL raise `CorruptionError`
naming that there was no match.

The reader SHALL report the resolved origin as `ArchiveInfo.prefix_kind` /
`ArchiveInfo.payload_offset` (`archive-data-model`), on both the detected and the
forced-format path.

#### Scenario: 7z SFX / start-offset matrix

| Case | Expected |
| --- | --- |
| Magic at open origin (offset 0) | Unchanged success path |
| Explicit start offset N with magic at N | Signature parsed at N; members listed; no forward scan performed |
| Forced `format=SEVEN_Z`, `MZ` stub, magic at N within `SFX_MAX`, header validates | Scan finds N; open succeeds |
| Forced `format=SEVEN_Z`, `MZ` stub, magic at N, header does not validate, no later VALID hit | Scan falls back to N; the parser reports the damage (truncated or CRC-broken) or opens (empty archive, `NextHeaderSize` 0) |
| Forced `format=SEVEN_Z`, decoy magic then a VALID payload within `SFX_MAX` | Earliest VALID wins |
| Forced `format=SEVEN_Z`, no magic within `SFX_MAX` | `CorruptionError` naming that there was no match |
| Forced `format=SEVEN_Z`, `MAX_VALIDATED_CANDIDATES` (256) candidates rejected, none VALID | `CorruptionError` naming that the candidate cap was reached |
| Packed streams after an SFX signature | Pack/header seeks use signature origin; members readable |
| Either path, magic at N | `info.payload_offset == N`, measured from the start of `source` |
