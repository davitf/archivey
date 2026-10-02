## ADDED Requirements

### Requirement: A RAR file cut inside a header SHALL list the members before the cut

When a RAR file ends after the first byte of a header and before its last, the archive
SHALL open and list the members whose headers precede that header. The cut SHALL then be
reported as `TruncatedError`: as `members_report().error`, and raised by `members()` and
`stream_members()` after the listed members. This SHALL hold for RAR 1.5-4 and RAR5, in
both access modes, and for a RAR5 header cut inside its CRC or its size field. It is the
same rule as a walk whose skip over packed data lands past the end of the file. In a
multi-volume set the message SHALL name the volume, because its byte offset is within
that volume.

Only a file that ends before the bytes a header declares is a cut. A declared size that
is invalid while its bytes are present SHALL stay `CorruptionError` at open.

With encrypted headers (`-hp`), a cut inside a salt or IV, or before a header's first
whole cipher block, is a cut as above. A cut after that block is a cut only once the
password is proven: in RAR5 by the archive's password check value, in RAR 1.5-4 by an
earlier encrypted header whose CRC16 matched. Before that proof, a wrong key also
decrypts a size that reads to the end of the file, so the open SHALL raise
`EncryptionError`. That happens inside the first encrypted header of a RAR 1.5-4
archive, and inside any header of a RAR5 archive whose encryption record has no check
value.

A file that ends exactly at a header boundary is not a cut: RAR 1.5-4 lists as complete,
and RAR5 lists and then emits `ARCHIVE_EOF_MARKER_MISSING`.

#### Scenario: header cut matrix

| Case | Expected |
| --- | --- |
| Plain RAR 1.5-4 or RAR5, cut inside any header | Members before it listed; `TruncatedError` after them |
| Plain, cut exactly at a header boundary | RAR 1.5-4: complete listing; RAR5: listing, then `ARCHIVE_EOF_MARKER_MISSING` |
| RAR3 size below 7, RAR5 size over 2 MiB, RAR5 size field over 10 bytes | `CorruptionError` at open |
| `-hp` RAR5 with a check value, cut inside any header | Members before it listed; `TruncatedError` after them |
| `-hp` RAR 1.5-4, cut inside a header after one whose CRC16 matched | Members before it listed; `TruncatedError` after them |
| `-hp` RAR 1.5-4, cut after the first encrypted header's first cipher block | `EncryptionError` at open |
| `-hp` RAR5 without a check value, cut after a header's first cipher block | `EncryptionError` at open |
| Later volume of a set cut inside a header | `TruncatedError` naming that volume |
