# seekable-decompressor-streams — deferred stream raises delta

## MODIFIED Requirements

### Requirement: Recoverable seek-index degradation is diagnostic data

When an XZ/lzip backward index or trailer scan fails but sequential
decompression remains safe, the system SHALL emit `SEEK_INDEX_DEGRADED` with
codec, scan kind, and public failure type, then use sequential fallback unless
policy escalates. The occurrence SHALL be aggregate-only on stream/reader
operation summaries, and SHALL reach the collector of the reader or stream that
opened the codec, so the caller's policy and callback apply. An escalated
occurrence SHALL raise only once the stream is consistent: a read raises before
it consumes, keeping the bytes it decoded for the next read, and a seek or size
query raises after it finishes, with the position where it left it. A caller
that catches the `DiagnosticRaisedError` SHALL be able to keep reading and get
the right bytes. Unsafe corruption SHALL remain a typed
`CorruptionError`/`TruncatedError`, not a recoverable diagnostic.

Every seek point SHALL resume on its own, whatever other points the table holds.
An XZ block point SHALL carry its stream's check type, where the stream's blocks
end, and the stream's decompressed and compressed ends; resuming from it SHALL
decode to where the blocks end, SHALL raise `CorruptionError` when the decoded
size disagrees with the stream index, and SHALL then continue sequentially past
the stream.

A stream's seek table SHALL hold at most a fixed number of entries (262 144). Past
it the table SHALL be thinned, not dropped: points kept at least a spacing apart in
decompressed bytes, chosen so the table falls to half the cap, with later points
kept at that spacing. A backward scan SHALL thin as it walks, never holding more
than the cap (nor a whole stream's blocks), and SHALL keep the last unit so the
size it reports stays exact. Thinning SHALL emit `SEEK_INDEX_DEGRADED` (failure
type `SeekTableThinned`). The cap is structural rather than a config field
because passing it costs seek speed, never a readable file.

#### Scenario: seek-index degradation matrix

| Case | Expected |
| --- | --- |
| Recoverable XZ index scan failure | `SEEK_INDEX_DEGRADED` collected/logged; stream falls back sequentially |
| Same issue resolves to `RAISE` | `DiagnosticRaisedError`; no fallback |
| That `DiagnosticRaisedError` caught, then reading on | Every byte, in order; a member digest or length check does not report a false fault |
| One stream's table thinned more than once | Recorded once; `RAISE` raises in every read, seek or size query that thins it, once per call |
| Corruption prevents correct sequential decoding | `CorruptionError` / `TruncatedError`, not diagnostic fallback |
| XZ index scan fails after a forward read recorded block points | A later seek from those points reads the right bytes to the end |
| Backward scan finds more units than the cap (lzip members, xz streams or blocks) | Kept units thinned by spacing, last one kept; `SEEK_INDEX_DEGRADED`; seeks read the right bytes |
| A thinned xz index leaves out streams after block points a forward read recorded | A seek from those points reads every stream after them |
| A forward read's per-stream xz scan finds more blocks than the cap | That stream's blocks thinned by spacing; `SEEK_INDEX_DEGRADED`; seeks read the right bytes |
| Forward read records more points than the cap | Table thinned by spacing; `SEEK_INDEX_DEGRADED`; later seeks read the right bytes |
| XZ blocks after a resume point decode to a size other than the stream index declares | `CorruptionError` |
