# format-directory — fewer exception types

## MODIFIED Requirements

### Requirement: Keep directory reader constraints as strict as archive readers

The directory reader SHALL enforce the same API-level constraints as real archive
readers even where the filesystem could permit more. Without
`MemberStreams.CONCURRENT`, a second overlapping member stream SHALL raise
`ArchiveyUsageError`. Without `MemberStreams.SEEKABLE`, member streams SHALL
report `seekable() is False`, `seek()` SHALL raise `io.UnsupportedOperation`,
and `tell()` remains available per `archive-reading`.

Code developed against a directory reader MUST behave the same when pointed at a
real archive; the directory backend therefore refuses everything a real archive
reader might refuse.

#### Scenario: directory uniformity matrix

| Case | Expected |
| --- | --- |
| One member stream is live, then another opens without `CONCURRENT` | `ArchiveyUsageError`, matching ZIP/TAR behavior |
| Member stream obtained without `SEEKABLE` | `seekable() is False`; `seek()` raises `io.UnsupportedOperation` despite real file backing |
| Same code later uses an archive reader | No dependency on directory-only leniency |
