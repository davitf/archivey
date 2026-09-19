# archive-reading — the spool limit on the config surface

## MODIFIED Requirements

### Requirement: Configuration reaches the reader through ArchiveyConfig

`open_archive()` SHALL accept cross-cutting configuration through `ArchiveyConfig`, and the
**source spool limit** SHALL reach the reader that way, alongside `listing_limits`. It SHALL
NOT be a per-call argument on `open_archive()` / `open_stream()` / `extract()`: source
handling applies to every read the reader performs, which is what distinguishes it from
`limits=`, an extraction-scoped argument.

The setting SHALL live on a **frozen `SpoolLimits` dataclass with an `UNLIMITED` classvar**,
matching `ExtractionLimits` and `ListingLimits` exactly, so the spool limit reads as the
third member of a family the caller has already met rather than a new shape to learn. Its
limit field SHALL take a byte count, the `UNLIMITED` sentinel, or none, so that a caller who
wants archivey never to use temporary storage can say so in one place. The spool directory
SHALL travel on the same object.

#### Scenario: spool configuration matrix

| Case | Expected |
| --- | --- |
| No setting given | The documented default limit applies |
| Setting given on `ArchiveyConfig` | Applies to every read that reader performs, including extraction |
| Caller passes the limit as a per-call argument | Not supported; the parameter does not exist |
| Caller sets the limit to none | No operation on that reader writes to temporary storage |
| Same configuration reused across readers | Supported; the values carry no per-reader state |
