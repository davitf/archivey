# logging — logger table illustrative delta

## MODIFIED Requirements

### Requirement: Logging under the archivey logger hierarchy

The system SHALL emit all log messages via `logging.getLogger("archivey")` and
children. It MUST NOT configure handlers, levels, filters, or formatters.

The loggers the library uses include, but are not limited to, the following. The
table is illustrative: a logger missing from it is not a contract violation.

| Logger | Events |
| --- | --- |
| `archivey.detection` | Format detection events |
| `archivey.normalization` | Path normalization changes, including warnings when `name` differs from `raw_name` |
| `archivey.extraction` | Extraction events and filter decisions |
| `archivey.diagnostics` | Default logger for a diagnostic's WARNING projection. An emit site that names a subsystem logger (`archivey.streams`, `archivey.integrity`, …) logs the WARNING there instead, and most do |
| `archivey.backends.*` | Backend-specific debug messages |

#### Scenario: logger-hierarchy matrix

| Case | Expected |
| --- | --- |
| Application configures no handlers on `archivey` or ancestors | No output by default; library installs no handler |
| Magic bytes conflict with extension | `logging.WARNING` on `archivey.detection` |
| Member-name normalization changes logical meaning relative to `raw_name` | Warning on `archivey.normalization` |
