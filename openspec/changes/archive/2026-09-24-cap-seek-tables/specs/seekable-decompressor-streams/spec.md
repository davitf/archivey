# seekable-decompressor-streams — cap the seek table delta

## MODIFIED Requirements

### Requirement: Recoverable seek-index degradation is diagnostic data

When an XZ/lzip backward index or trailer scan fails but sequential
decompression remains safe, the system SHALL emit `SEEK_INDEX_DEGRADED` with
codec, scan kind, and public failure type, then use sequential fallback unless
policy escalates. The occurrence SHALL be aggregate-only on stream/reader
operation summaries. Unsafe corruption SHALL remain a typed
`CorruptionError`/`TruncatedError`, not a recoverable diagnostic.

The system SHALL NOT resume from a seek point whose resume depends on points
after it (an XZ block point, which resumes a chain of every block point after it)
unless the table lists every block and stream that chain runs through. When an
index build fails, when a table is thinned, or when a thinned index joins block
points a forward read recorded, block points SHALL be replaced by the start of
their stream, which decodes forward on its own.

A stream's seek table SHALL hold at most a fixed number of entries (262 144). Past
it the table SHALL be thinned, not dropped: points kept at least a spacing apart in
decompressed bytes, chosen so the table falls to half the cap, with later points
kept at that spacing. Block points SHALL NOT be dropped one at a time. A backward
scan SHALL thin as it walks, never holding more than the cap, and SHALL keep the
last unit so the size it reports stays exact. Thinning SHALL emit
`SEEK_INDEX_DEGRADED` (failure type `SeekTableThinned`). The cap is structural
rather than a config field because passing it costs seek speed, never a readable
file.

#### Scenario: seek-index degradation matrix

| Case | Expected |
| --- | --- |
| Recoverable XZ index scan failure | `SEEK_INDEX_DEGRADED` collected/logged; stream falls back sequentially |
| Same issue resolves to `RAISE` | `DiagnosticRaisedError`; no fallback |
| Corruption prevents correct sequential decoding | `CorruptionError` / `TruncatedError`, not diagnostic fallback |
| XZ index scan fails after a forward read recorded block points | Those points become stream starts; a later seek reads the right bytes to the end |
| Backward scan finds more units than the cap (lzip members, xz streams) | Kept units thinned by spacing, last one kept; `SEEK_INDEX_DEGRADED`; seeks read the right bytes |
| An xz file's blocks would pass the cap | Every stream kept as its start only, then thinned by spacing; seeks read the right bytes |
| A thinned xz index joins block points a forward read recorded | Those points become stream starts; a later seek reads the right bytes to the end |
| A forward read's per-stream xz scan finds more blocks than the cap | That stream gets its start only; `SEEK_INDEX_DEGRADED`; seeks into it decode from its start |
| Forward read records more points than the cap | Block points become stream starts, then the table is thinned by spacing; `SEEK_INDEX_DEGRADED`; later seeks read the right bytes |
