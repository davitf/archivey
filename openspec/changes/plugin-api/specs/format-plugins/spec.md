## ADDED Requirements

### Requirement: A package adds a format through archivey.plugins

The system SHALL let a package outside archivey add a read-only format by subclassing
`archivey.plugins.FormatPlugin` and `archivey.plugins.PluginReader` and calling
`archivey.plugins.register_format()`. A plugin reader SHALL NOT subclass or see
`BaseArchiveReader`. An internal adapter SHALL present it as an ordinary
`ArchiveReader`.

#### Scenario: plugin format through the public API

| Case | Expected |
| --- | --- |
| A CPIO plugin is registered; `open_archive("a.cpio")` | Reader with `format.container` the plugin's `PluginContainer` member; `members()`, `open()`, `stream_members()`, `extract_all()` work |
| Same, `detect_format("a.cpio")` | The plugin format |
| Same, `format_availability("cpio")` | `FULL` |
| No plugin registered, `open_archive("a.cpio")` | `FormatDetectionError`, as today |

### Requirement: Plugin containers subclass PluginContainer

`ArchiveFormat.container` SHALL accept a member of a `PluginContainer` subclass and keep
it unchanged. `register_format()` SHALL refuse with `ArchiveyUsageError` a container
value equal to a built-in or registered one, and an extension a built-in format claims.

#### Scenario: registration refusals

| Case | Expected |
| --- | --- |
| Plugin container value `"zip"` | `ArchiveyUsageError` |
| Plugin extension `".tar"` | `ArchiveyUsageError` |
| Same plugin registered twice | `ArchiveyUsageError` |

### Requirement: Built-in detection signals outrank plugin signals

A plugin magic or probe match SHALL count only when no built-in format matched at the
same or a higher confidence. A plugin probe SHALL receive only the prefix detection
already read.

#### Scenario: plugin magic does not change built-in detection

| Case | Expected |
| --- | --- |
| A plugin declares magic `PK\x03\x04`; a ZIP is detected | `ArchiveFormat.ZIP` |

### Requirement: Plugin member data is checked like built-in member data

The adapter SHALL verify every plugin member stream against the listed size and the
digests the plugin returns, and SHALL apply the built-in name normalization, name checks,
listing limits and extraction limits to plugin members.

#### Scenario: plugin data checks

| Case | Expected |
| --- | --- |
| Plugin stream shorter than the listed size, read to the end | `TruncatedError` |
| Plugin returns a CRC-32 that does not match | `CorruptionError` |
| Plugin lists a member named `../x`; `extract_all` | Refused as for a built-in format |
