## MODIFIED Requirements

### Requirement: Diagnostic codes and typed contexts

The catalog SHALL include a code for a **probe-only** format claim that a later
decode failure showed was unconfirmed:

| Code | Context |
| --- | --- |
| `PROBE_FORMAT_UNCONFIRMED` | `UnconfirmedFormatContext` with `chosen_by="content_probe"` |

This is **not** `EXTENSION_FORMAT_UNCONFIRMED`: that code keys on
`detected_by="extension"` and an empty listing. Probe-only read failures are a
different provenance channel — `detected_by="content_probe"` at `GUESS` — and must
not double-report under the extension code.

`UnconfirmedFormatContext.chosen_by` SHALL accept `"content_probe"` in addition to
`"argument"` and `"extension"`.

#### Scenario: probe-format-unconfirmed matrix

| Case | Expected |
| --- | --- |
| Probe-only (`GUESS`) single-file read raises | `PROBE_FORMAT_UNCONFIRMED` emitted; `chosen_by="content_probe"` |
| Extension-only empty listing | Still `EXTENSION_FORMAT_UNCONFIRMED` only — unchanged |
| Probe + `.br` (`PROBABLE`) read raises | No `PROBE_FORMAT_UNCONFIRMED` — format was corroborated |
| Probe-only read succeeds | No diagnostic |

## ADDED Requirements

### Requirement: A probe-only decode failure is filterable as unconfirmed

When a single-file decode fails and `ArchiveyError.format_unconfirmed` is true (see
`error-handling`), the system SHALL also emit `PROBE_FORMAT_UNCONFIRMED` so callers
that filter diagnostics — without inspecting exception attributes — see the same
fact. The exception attribute and the diagnostic are two views of one provenance;
neither replaces the other.

#### Scenario: dual channel

| Case | Expected |
| --- | --- |
| Probe-only decode failure | `exc.format_unconfirmed is True` **and** a `PROBE_FORMAT_UNCONFIRMED` diagnostic |
| Corroborated decode failure | `exc.format_unconfirmed is False`; no probe-unconfirmed diagnostic |
