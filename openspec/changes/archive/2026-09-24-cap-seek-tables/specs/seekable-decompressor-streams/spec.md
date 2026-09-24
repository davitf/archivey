# seekable-decompressor-streams — cap the seek table delta

## MODIFIED Requirements

### Requirement: Recoverable seek-index degradation is diagnostic data

When an XZ/lzip backward index or trailer scan fails but sequential
decompression remains safe, the system SHALL emit `SEEK_INDEX_DEGRADED` with
codec, scan kind, and public failure type, then use sequential fallback unless
policy escalates. The occurrence SHALL be aggregate-only on stream/reader
operation summaries. Unsafe corruption SHALL remain a typed
`CorruptionError`/`TruncatedError`, not a recoverable diagnostic.

After a failed index build the system SHALL NOT resume from a seek point whose
resume depends on the complete index (an XZ block point): such points SHALL be
dropped and no more recorded. Points that decode forward on their own (a stream
or member start) MAY stay.

A stream's seek table SHALL hold at most a fixed number of entries (262 144). A
backward scan that would pass it SHALL stop before storing further entries, and
points a forward read records SHALL NOT grow the table past it. Passing it SHALL
drop the table to its origin, stop indexing that stream, and emit
`SEEK_INDEX_DEGRADED` (failure type `SeekIndexTooLarge`); seeks then decode from
the start. The cap is structural rather than a config field because passing it
costs seek speed, never a readable file.

#### Scenario: seek-index degradation matrix

| Case | Expected |
| --- | --- |
| Recoverable XZ index scan failure | `SEEK_INDEX_DEGRADED` collected/logged; stream falls back sequentially |
| Same issue resolves to `RAISE` | `DiagnosticRaisedError`; no fallback |
| Corruption prevents correct sequential decoding | `CorruptionError` / `TruncatedError`, not diagnostic fallback |
| XZ index scan fails after a forward read recorded block points | Those points are dropped; a later seek reads the right bytes to the end |
| Backward scan finds more units than the cap (xz blocks or streams, lzip members) | Scan stops at the cap; `SEEK_INDEX_DEGRADED`; seeks decode from the start |
| Forward read records more points than the cap | Table dropped to its origin; `SEEK_INDEX_DEGRADED`; later seeks read the right bytes |
