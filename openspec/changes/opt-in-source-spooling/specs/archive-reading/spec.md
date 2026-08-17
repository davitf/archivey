# archive-reading — spooling policy on the config surface

## MODIFIED Requirements

### Requirement: Configuration reaches the reader through ArchiveyConfig

`open_archive()` SHALL accept cross-cutting configuration through `ArchiveyConfig`, and the
**source spooling policy** SHALL be one of its fields, alongside `listing_limits`. It SHALL
NOT be a per-call argument on `open_archive()` / `open_stream()` / `extract()`: source
handling applies to every read the reader performs, which is what distinguishes it from
`limits=`, an extraction-scoped argument.

The policy SHALL be a frozen value object so a caller can build one, reuse it across
readers, and compare it, and SHALL expose a permissive and a restrictive named constructor
so the common cases do not require assembling fields.

#### Scenario: spooling configuration matrix

| Case | Expected |
| --- | --- |
| No policy given | Defaults apply: tool-tax permitted, capability refused, a documented byte limit |
| Policy given on `ArchiveyConfig` | Applies to every read that reader performs, including extraction |
| Caller passes a policy as a per-call argument | Not supported; the parameter does not exist |
| Same policy object reused across readers | Supported; the object is frozen and carries no per-reader state |
