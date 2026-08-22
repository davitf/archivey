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

### Requirement: Named diagnostic policy presets and taxonomy-growth contract

The system SHALL provide named `DiagnosticPolicy` constructors so a caller can express
a coarse strictness without enumerating the taxonomy:

```python
ARCHIVE_INTEGRITY_CODES: frozenset[DiagnosticCode]

DiagnosticPolicy.strict()    # RAISE on ARCHIVE_INTEGRITY_CODES, COLLECT otherwise
DiagnosticPolicy.pedantic()  # RAISE on every code
```

`ARCHIVE_INTEGRITY_CODES` SHALL be a public frozen set covering the codes that report
the archive's own bytes or metadata as anomalous:

| In `ARCHIVE_INTEGRITY_CODES` | Excluded |
| --- | --- |
| `MEMBER_NAME_NORMALIZED`, `MEMBER_NAME_ENCODING_INFERRED`, `MEMBER_NAME_BIDI_CONTROL`, `FORMAT_EXTENSION_CONFLICT`, `EXTENSION_FORMAT_UNCONFIRMED`, `SCAN_DIRECTORY_VANISHED`, `SCAN_ENTRY_VANISHED`, `ARCHIVE_EOF_MARKER_MISSING`, `ARCHIVE_TRAILING_DATA`, `MEMBER_TIMESTAMP_INVALID`, `SYMLINK_TARGET_UNAVAILABLE`, `DIGEST_UNVERIFIABLE`, `SEEK_INDEX_DEGRADED` | `EMPTY_ARCHIVE` (an empty archive is legitimate), `EXPLICIT_FORMAT_LISTED_EMPTY`, `ENCODING_ARGUMENT_UNUSED`, `PASSWORD_ARGUMENT_UNUSED`, `STREAM_REWIND_REDECOMPRESSES`, `PROBE_FORMAT_UNCONFIRMED` |

Each exclusion is deliberate, and the reason SHALL be recorded so the boundary is not
rediscovered: `EMPTY_ARCHIVE` because an empty archive is legitimate and this spec
forbids treating zero members as an error; `ENCODING_ARGUMENT_UNUSED` and
`PASSWORD_ARGUMENT_UNUSED` because they report argument hygiene, and a pipeline that
speculatively passes a password to every call would otherwise raise on every
unencrypted archive; `EXPLICIT_FORMAT_LISTED_EMPTY` because `format=` is an override
and an override that halts the caller is not an override; and
`STREAM_REWIND_REDECOMPRESSES` because it reports the caller's access pattern rather
than the archive, and is most useful as a deliberately targeted tripwire;
`PROBE_FORMAT_UNCONFIRMED` because it is emitted while stamping a typed
`TruncatedError` / `CorruptionError` that already carries `format_unconfirmed=True`,
and putting it in `strict` would replace that typed error with `DiagnosticRaisedError`.

Presets SHALL return ordinary frozen `DiagnosticPolicy` values with per-code
overrides — no new resolution axis, and no field on `Diagnostic`. A caller MAY build
its own policy from `ARCHIVE_INTEGRITY_CODES`.

**Taxonomy growth.** New `DiagnosticCode` members MAY be added in minor releases. A
policy with `default=RAISE` therefore SHALL NOT be described as version-stable: a
caller running it starts raising on events their working program never produced. The
documentation SHALL state this, and SHALL present `strict()` — whose membership is
versioned alongside the taxonomy — as the recommended strict mode. Removing a code
remains a breaking change.

#### Scenario: preset matrix

| Case | Expected |
| --- | --- |
| `strict()`, archive with a truncated TAR trailer | `DiagnosticRaisedError` on `ARCHIVE_EOF_MARKER_MISSING` |
| `strict()`, unencrypted archive opened with `password=` | No raise; `PASSWORD_ARGUMENT_UNUSED` is collected |
| `pedantic()`, same call | `DiagnosticRaisedError` on `PASSWORD_ARGUMENT_UNUSED` |
| `strict()`, legitimately empty tar | No raise; `EMPTY_ARCHIVE` collected |
| Preset value compared to an equivalent hand-built policy | Equal; presets add no resolution axis |
| A new code added in a later minor release | `strict()` membership is explicit; a `default=RAISE` policy silently gains it |

#### Scenario: probe code stays out of strict

| Case | Expected |
| --- | --- |
| `PROBE_FORMAT_UNCONFIRMED in ARCHIVE_INTEGRITY_CODES` | False |
| `DiagnosticPolicy.strict()` disposition for that code | COLLECT (via default) |

## ADDED Requirements

### Requirement: A probe-only decode failure is filterable as unconfirmed

When a single-file decode fails and `ArchiveyError.format_unconfirmed` is true (see
`error-handling`), the system SHALL also emit `PROBE_FORMAT_UNCONFIRMED` so callers
that filter diagnostics — without inspecting exception attributes — see the same
fact. The exception attribute and the diagnostic are two views of one provenance;
neither replaces the other.

`PROBE_FORMAT_UNCONFIRMED` SHALL NOT be a member of the `ARCHIVE_INTEGRITY_CODES`
strict set: it is emitted while stamping a typed `TruncatedError` / `CorruptionError`,
and putting it in `strict` would replace that typed error with `DiagnosticRaisedError`.
Default disposition is COLLECT. When a caller's policy resolves this code to RAISE
(notably `DiagnosticPolicy.pedantic()`), the emit SHALL surface the same typed error
via `escalate_as` (carrying `format_unconfirmed=True`) rather than
`DiagnosticRaisedError`. The diagnostic is emitted at most once per reader.

#### Scenario: dual channel

| Case | Expected |
| --- | --- |
| Probe-only decode failure | `exc.format_unconfirmed is True` **and** a `PROBE_FORMAT_UNCONFIRMED` diagnostic |
| Corroborated decode failure | `exc.format_unconfirmed is False`; no probe-unconfirmed diagnostic |
| `pedantic()`, probe-only decode failure | Same typed `TruncatedError`/`CorruptionError` with `format_unconfirmed=True` — not `DiagnosticRaisedError` |
| Three retried reads on one probe-only member | Diagnostic count stays 1; each exception still has `format_unconfirmed=True` |
