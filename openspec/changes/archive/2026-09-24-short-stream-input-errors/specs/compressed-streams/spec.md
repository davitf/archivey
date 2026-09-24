# compressed-streams — short input to the native stream decoders delta

## MODIFIED Requirements

### Requirement: Returned streams translate decompression errors

The system SHALL wrap backend streams so decompression failures surface as
Archivey exceptions: corrupt data as `CorruptionError`, unexpected end-of-input
as `TruncatedError`, and source seek requirements as the documented non-seekable
error. No raw backend exception SHALL escape. For zstd specifically,
`compression.zstd.ZstdError` SHALL map to `CorruptionError`, and its truncation
`EOFError` SHALL map to `TruncatedError`.

A source that ends before its first complete header is end-of-input too. For the
native xz, lzip and unix-compress decoders, a source that is empty, or holds only a
prefix of the format's magic bytes, SHALL raise `TruncatedError`; a short source whose
bytes cannot begin that format SHALL raise `CorruptionError`. Neither SHALL decode as a
valid empty stream.

#### Scenario: decompression error matrix

| Case | Expected |
| --- | --- |
| Corrupt compressed stream is read | `CorruptionError` with backend exception as `__cause__` |
| Compressed stream ends mid-data | `TruncatedError` |
| Zstd stream ends before end-of-frame marker | `TruncatedError`, not a silent short read |
| Zstd checksum frame is corrupted | `CorruptionError` with backend `ZstdError` as `__cause__` |
| Empty source, or only a prefix of the magic, to xz / lzip / unix-compress | `TruncatedError` |
| Source shorter than a header whose bytes are not the format's magic (xz / lzip / unix-compress) | `CorruptionError`, never `b""` |
