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
listing**; the probe code keys on **probe-only provenance** — `detected_by="content_probe"`
with nothing corroborating the claim (no matching extension, no inner-TAR upgrade) — **and
a decode failure**. A probe-only read failure MUST NOT double-report under the extension
code.

**Confidence is not part of that trigger.** The probe code fires on a probe-only failure at
*any* `DetectionConfidence`, so an LZMA Alone hit (always `PROBABLE`) and a compressed-first
Brotli hit (`PROBABLE`) are stamped exactly as an uncorroborated `GUESS` hit is. Confidence
grades how strong the evidence looked; this code answers whether anything independent
corroborated it. Keying the diagnostic on confidence left 53% of measured fabrications
unsignalled, and made a probe's confidence value double as a switch for error behaviour.
This matches `error-handling`'s `format_unconfirmed`, and the two SHALL agree by
construction: the diagnostic is emitted while stamping that attribute, never on a separate
test.

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
| Probe-only single-file read raises, uncorroborated `GUESS` | `PROBE_FORMAT_UNCONFIRMED` with `chosen_by="content_probe"` |
| Probe-only single-file read raises, uncorroborated **`PROBABLE`** (compressed-first Brotli) | `PROBE_FORMAT_UNCONFIRMED` too — **changed**; confidence is not the trigger |
| Probe-only **LZMA Alone** read raises (always `PROBABLE`) | `PROBE_FORMAT_UNCONFIRMED` — **changed**; previously unsignalled |
| Extension-only empty listing | Still `EXTENSION_FORMAT_UNCONFIRMED` only — unchanged |
| Probe + `.br` (`PROBABLE`) read raises | No `PROBE_FORMAT_UNCONFIRMED` — the format was corroborated, and corroboration is still what matters |
| Probe hit upgraded to `TAR_*` by the inner-TAR probe, read raises | No `PROBE_FORMAT_UNCONFIRMED` — the upgrade is independent corroboration |
| Probe-only read succeeds | No diagnostic |
