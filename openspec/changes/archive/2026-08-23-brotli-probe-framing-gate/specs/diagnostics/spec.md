## MODIFIED Requirements

### Requirement: Immutable diagnostic values with stable codes and safe typed context

Every advisory event SHALL be an immutable `Diagnostic`: opaque process-local
`occurrence_id`, stable `DiagnosticCode`, `DiagnosticSeverity`, human `message`,
and code-specific frozen `DiagnosticContext`. Codes are the machine contract;
messages are not stable.

Contexts are a closed typed union of JSON-safe immutable scalar/tuple values.
Raw bytes use an explicitly named base64 field. `to_dict()` on diagnostic and
context SHALL be `json.dumps`-safe without a custom encoder.

| Code | Variant and required fields |
| --- | --- |
| `MEMBER_NAME_NORMALIZED` | `NameNormalizationContext`: `kind="name_normalization"`, `archive_name`, `member_name`, `member_id`, `raw_name_base64`, `presented_name`, `normalized_name` |
| `MEMBER_NAME_ENCODING_INFERRED` | `NameEncodingContext`: `kind="name_encoding"`, `archive_name`, `member_name`, `member_id`, `raw_name_base64`, `inferred_encoding`, `declared_encoding` |
| `MEMBER_NAME_BIDI_CONTROL` | `MemberNameControlsContext`: `kind="member_name_controls"`, `archive_name`, `member_name`, `member_id`, `raw_name_base64`, `controls` |
| `FORMAT_EXTENSION_CONFLICT` | `FormatConflictContext`: `kind="format_conflict"`, `source_name`, `extension`, `extension_format`, `detected_format` |
| `EXPLICIT_FORMAT_LISTED_EMPTY` | `UnconfirmedFormatContext`: `kind="unconfirmed_format"`, `archive_name`, `format`, `chosen_by="argument"`, `detected_format` |
| `EXTENSION_FORMAT_UNCONFIRMED` | `UnconfirmedFormatContext`: `kind="unconfirmed_format"`, `archive_name`, `format`, `chosen_by="extension"`, `detected_format=None` |
| `PROBE_FORMAT_UNCONFIRMED` | `UnconfirmedFormatContext`: `kind="unconfirmed_format"`, `archive_name`, `format`, `chosen_by="content_probe"`, `detected_format` |
| `EMPTY_ARCHIVE` | `EmptyArchiveContext`: `kind="empty_archive"`, `archive_name`, `format` |
| `ENCODING_ARGUMENT_UNUSED` | `UnusedArgumentContext`: `kind="unused_argument"`, `archive_name`, `argument="encoding"`, `format`, `reason` |
| `PASSWORD_ARGUMENT_UNUSED` | `UnusedArgumentContext`: `kind="unused_argument"`, `archive_name`, `argument="password"`, `format`, `reason` |
| `SCAN_DIRECTORY_VANISHED` | `ScanRaceContext`: `kind="scan_race"`, `archive_name`, `relative_path`, `entry_kind="directory"` |
| `SCAN_ENTRY_VANISHED` | `ScanRaceContext`: `kind="scan_race"`, `archive_name`, `relative_path`, `entry_kind="entry"` |
| `ARCHIVE_EOF_MARKER_MISSING` | `ArchiveEofContext`: `kind="archive_eof"`, `archive_name`, `format`, `expected_marker`, `expected_bytes`, `observed_bytes`, `observed_kind` |
| `ARCHIVE_TRAILING_DATA` | `ArchiveEofContext`: `kind="archive_eof"`, `archive_name`, `format`, `expected_marker="zeros_to_eof"`, `expected_bytes=0`, `observed_bytes`, `observed_kind="nonzero"` |
| `MEMBER_TIMESTAMP_INVALID` | `MemberTimestampContext`: `kind="member_timestamp"`, `archive_name`, `member_name`, `member_id`, `field`, `source`, `value_repr` |
| `SYMLINK_TARGET_UNAVAILABLE` | `SymlinkTargetContext`: `kind="symlink_target"`, `archive_name`, `member_name`, `member_id`, `reason` |
| `DIGEST_UNVERIFIABLE` | `DigestContext`: `kind="digest"`, `archive_name`, `member_name`, `member_id`, `algorithm`, `reason` |
| `SEEK_INDEX_DEGRADED` | `SeekIndexContext`: `kind="seek_index"`, `archive_name`, `member_name`, `member_id`, `codec`, `scan`, `error_type` |
| `STREAM_REWIND_REDECOMPRESSES` | `StreamRewindContext`: `kind="stream_rewind"`, `archive_name`, `member_name`, `member_id`, `codec`, `from_offset`, `to_offset`, `accelerator` |

(`str | None` / `int | None` as in the typed variants.) `DiagnosticContext` is
exactly this union — no backend-defined variants. `observed_kind` ∈
`{"absent","short","nonzero"}`. `expected_marker` is symbolic (`"two_zero_blocks"` for the trailer check,
`"zeros_to_eof"` for the strict trailing-bytes check, whose `observed_bytes` is the
offset of the first non-zero byte past the trailer). `member_id` MAY be `None` only before registration.
`controls` SHALL be the comma-joined `U+XXXX` spellings of the bidi codepoints
found, in the order they occur, so a caller can tell an override from a mark
without re-scanning the name. `chosen_by` ∈ `{"argument","extension","content_probe"}`;
`detected_format` is `None` when detection refuses the bytes outright.

`PROBE_FORMAT_UNCONFIRMED` is a **separate code**, not a widening of
`EXTENSION_FORMAT_UNCONFIRMED`. The two describe different provenance and fire on
different events: the extension code keys on `detected_by="extension"` **and an empty
listing**; the probe code keys on `detected_by="content_probe"` at `GUESS` confidence
**and a decode failure**. A probe-only read failure MUST NOT double-report under the
extension code.

`ExtractionOutcomeContext`, `NameCollisionContext` and `NameSanitizedContext` SHALL
NOT exist: per-member extraction outcomes are carried by `ExtractionResult` (see
`safe-extraction`), not by this channel.

No diagnostic surface SHALL contain passwords, candidates, provider returns, keys,
KDF material, or decrypted secrets. `PASSWORD_ARGUMENT_UNUSED` therefore records
that a password argument was supplied and unused — never how many candidates, and
never any candidate value.

Copies on multiple surfaces MAY share `occurrence_id` by value; object identity
and cross-run id stability are not promised.

#### Scenario: value-model matrix

| Case | Expected |
| --- | --- |
| Name normalization | `MEMBER_NAME_NORMALIZED` + typed JSON-safe context; no backend/mutable mapping |
| Same occurrence on aggregate + member | Same `occurrence_id`; value equality; no object-identity promise |
| Encrypted symlink unavailable | May use reason `"password_required"` + member name; no secret material |
| Member blocked by a universal/policy check | No diagnostic; a `BLOCKED` `ExtractionResult` is the whole record |
| `password=["a","b"]` on a format with no encryption | `PASSWORD_ARGUMENT_UNUSED`; context carries no candidate value and no count |
| Non-zero byte past a complete TAR trailer under `strict_archive_eof` | `ARCHIVE_TRAILING_DATA` sharing `ArchiveEofContext`; distinguished by `expected_marker` |
| Probe-only (`GUESS`) single-file read raises | `PROBE_FORMAT_UNCONFIRMED` with `chosen_by="content_probe"` |
| Extension-only empty listing | Still `EXTENSION_FORMAT_UNCONFIRMED` only — unchanged |
| Probe + `.br` (`PROBABLE`) read raises | No `PROBE_FORMAT_UNCONFIRMED` — the format was corroborated |
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

That once-per-reader bound SHALL hold under **every** policy, RAISE included: an
escalating emit MUST NOT leave a second count, retention, log line or callback behind on
a later occurrence.

The per-code policy contract's deduplication rule (*"deduplication is a presentation
concern; escalation is not"*) is what makes that safe to state. Its guarantee is that no
qualifying occurrence passes **silently** under RAISE — a guard that disarms after firing
once is not a guard. For this code the raise does not come from the diagnostic: every
qualifying occurrence re-raises the stamped typed error with `format_unconfirmed=True`
whether or not the diagnostic fires again, so the second and later reads stop the caller
exactly as the first did. The escalation obligation is therefore met by the read path
itself, and re-emitting would buy a duplicate record rather than a stop.

#### Scenario: dual channel

| Case | Expected |
| --- | --- |
| Probe-only decode failure | `exc.format_unconfirmed is True` **and** a `PROBE_FORMAT_UNCONFIRMED` diagnostic |
| Corroborated decode failure | `exc.format_unconfirmed is False`; no probe-unconfirmed diagnostic |
| `pedantic()`, probe-only decode failure | Same typed `TruncatedError`/`CorruptionError` with `format_unconfirmed=True` — not `DiagnosticRaisedError` |
| Three retried reads on one probe-only member | Diagnostic count stays 1; each exception still has `format_unconfirmed=True` |
| `pedantic()`, three retried reads on one probe-only member | Count and retention stay 1; every read still raises the typed error with `format_unconfirmed=True` |
