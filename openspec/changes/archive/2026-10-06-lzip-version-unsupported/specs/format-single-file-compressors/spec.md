## ADDED Requirements

### Requirement: An lzip member in another version is unsupported

archivey SHALL read lzip version 1 only. A member whose header has the `LZIP` magic and
any other version byte, version 0 (lzip before 1.0) included, SHALL raise
`UnsupportedFeatureError`, not `CorruptionError`, and SHALL NOT be decoded. A full
`LZIP` magic SHALL start a member after a version-1 member as at the start of the file,
so a version-0 member after a version-1 one is refused, not read past as trailing data.
This matches `lzip`, which reports such a member as an unsupported version.

A seek SHALL NOT skip a member the forward read refuses: a seek to or past it raises
`UnsupportedFeatureError` too. The size and CRC-32 that the metadata probe reads at
open are unknown for such a file, and the open-time one-byte read raises
`UnsupportedFeatureError` when the first member is the one refused.

#### Scenario: lzip versions

| Source | Expected |
| --- | --- |
| A member in version 0, 2 or 255, anywhere in the file | A read or a seek that reaches it raises `UnsupportedFeatureError` |
| A version-1 member + `LZIP` and a version byte, with no dictionary byte | `TruncatedError` on a read and on a seek alike |
