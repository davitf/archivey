# archive-reading — the spool limit on the config surface

## MODIFIED Requirements

### Requirement: Configuration reaches the reader through ArchiveyConfig

`open_archive()` SHALL accept cross-cutting configuration through `ArchiveyConfig`, and the
**source spool limit** SHALL reach the reader that way, alongside `listing_limits`. It SHALL
NOT be a per-call argument on `open_archive()` / `open_stream()` / `extract()`: source
handling applies to every read the reader performs, which is what distinguishes it from
`limits=`, an extraction-scoped argument.

The setting SHALL be expressible in a single field whose three values are a byte count, an
unlimited sentinel, and none, so that a caller who wants archivey never to use temporary
storage can say so in one place. The spool directory SHALL be settable alongside it.

#### Scenario: spool configuration matrix

| Case | Expected |
| --- | --- |
| No setting given | The documented default limit applies |
| Setting given on `ArchiveyConfig` | Applies to every read that reader performs, including extraction |
| Caller passes the limit as a per-call argument | Not supported; the parameter does not exist |
| Caller sets the limit to none | No operation on that reader writes to temporary storage |
| Same configuration reused across readers | Supported; the values carry no per-reader state |
