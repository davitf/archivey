# Diagnostics

## Purpose

Advisory library events as immutable, policy-controlled data: stable codes, typed
JSON-safe context, bounded retention, and lifecycle-aware aggregation across
detection, readers, streams, and extraction.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | `reader.diagnostics` / stream-filtered views (observables) |
| `format-detection` | Standalone `FormatInfo.diagnostics`; open handoff of one collector |
| `safe-extraction` | Extraction report ranges over the shared collector |
| `access-mode-and-cost` | Runtime events must not mutate `CostReceipt` |
| `logging` | WARNING projection; handlers run unlocked |
| `reader-concurrency` | Callbacks / providers hold no Archivey locks |
## Requirements

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
without re-scanning the name. `chosen_by` ∈ `{"argument","extension"}`;
`detected_format` is `None` when detection refuses the bytes outright.

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

### Requirement: Exact bounded diagnostic summaries

`DiagnosticSummary` snapshots SHALL expose `total_count`, exact per-code `counts`,
retained occurrences in emission order, and `dropped_count`. Counts include every
emitted event regardless of disposition/retention. `dropped_count` = aggregate
occurrences not retained.

Each standalone detection, reader lifetime, top-level extraction, or standalone
stream SHALL enforce one `ArchiveyConfig.max_retained_diagnostic_references`
budget (default 256) across every library-retained reference for that collector.
Aggregate retention = one slot; one eligible object attachment = another.
Order: aggregate first, then most-specific attachment. No attachment without a
retained aggregate. Exact counters do not consume slots.

Snapshots are freshly created, bounded, never mutated. Caller-retained snapshots
and caller-created member copies are outside the library budget.

**Watermark (implementer):** an internal operation watermark consumes no slot and
copies no occurrence; a ranged summary computes counter deltas and selects from
already-retained aggregate entries.

#### Scenario: retention matrix

| Case | Expected |
| --- | --- |
| Member diagnostic, ≥2 slots left | Aggregate + member attachment; 1 slot → aggregate only |
| Budget exhausted, more events | No further detail/attachment; counts stay exact |
| `before = reader.diagnostics`, more events, `after = …` | `before` unchanged; `after` includes later counts in order |

### Requirement: Lifecycle-aware aggregation and attachment

The system SHALL own diagnostic aggregation per lifetime as follows:

| Lifetime | Collector ownership |
| --- | --- |
| Standalone `detect_format` | One collector; final summary on `FormatInfo` |
| `open_archive` + auto-detect | Prospective-reader collector created before detection, passed in, owned by successful reader — no seed/merge/replay/copy. Same counters, retained tuple, ids, order, one-time budget charges |
| Reader-owned stream | Operation-filtered view over the reader collector (no second aggregate retain) |
| Standalone stream | Own stream-lifetime collector |
| Top-level `extract()` | One collector for the whole call (see `safe-extraction`) |

Attachment rules:

- Natural member-metadata diagnostics MAY attach to `ArchiveMember.diagnostics`
  under the shared budget.
- `ExtractionResult` has **no** diagnostics field, and no per-member extraction
  outcome is emitted as a diagnostic at all: `status`, `error`, `requested_path`,
  `presented_name` and the failure-group fields are the whole record.
- Detection conflict attaches to `FormatInfo`.
- Runtime rewind, seek-index degradation, scan race, archive EOF: aggregate-only —
  never attached to frozen `CostReceipt` or `ArchiveInfo`.

`ExtractionReport.diagnostics` SHALL remain a real summary: reading an archive during
extraction still emits reading diagnostics (invalid timestamps, unresolvable symlinks,
unverifiable digests, rewinds), and those stay in the aggregate. What leaves the
channel is the per-member *extraction outcome*, not everything observed while
extracting.

#### Scenario: lifetime matrix

| Case | Expected |
| --- | --- |
| `open_archive` detects conflict, opens reader | One collector/budget; conflict one aggregate slot; visible on reader summary; no copy |
| Top-level `extract()` detect→open→extract | One collector from before detection; report is watermark range; no phase-local merge |
| Reader-owned stream rewinds | On stream op snapshot + cumulative reader; `CostReceipt`/`ArchiveInfo` unchanged |
| Extraction hits a member with an invalid timestamp and a blocked member | Timestamp diagnostic in the report summary; the block appears only as a `BLOCKED` result |

### Requirement: Complete per-code policy and delivery contract

The system SHALL provide a frozen `DiagnosticPolicy` with a default disposition
and immutable per-code overrides. The only dispositions SHALL be `IGNORE`,
`COLLECT`, and `RAISE` (no severity/logger matching).

| Disposition | Counts | Retain/attach | WARNING log | Callback | Raise |
| --- | --- | --- | --- | --- | --- |
| `IGNORE` | yes | no | no | no | no |
| `COLLECT` | yes | budget permitting | yes | if configured | no |
| `RAISE` | yes | budget permitting | yes | if configured | `DiagnosticRaisedError` |

Per event: validate typed payload → resolve policy → under collector lock allocate
id, build immutable value, update counts/retention → **release all locks** → log →
synchronous callback on calling thread → escalate. Logs/callbacks see
already-updated state, in emission order.

| Failure | Behavior |
| --- | --- |
| Logging-handler exception | Propagates; blocks later callback/escalation |
| Callback exception | Propagates unchanged; not under `OnError.CONTINUE`; blocks later `DiagnosticRaisedError`; operation still halted |

No collector/reader/stream/backend/registry lock while calling handlers/callbacks.
Callbacks MAY read snapshots; same-emitting-reader/stream operational reentry →
`UnsupportedOperationError`; other readers OK.

**Deduplication is a presentation concern; escalation is not.** Where a code is
documented as recorded *at most once* per stream or per reader, that bound SHALL apply to
counting, retention, logging and callbacks only. The configured policy SHALL be evaluated
on **every** occurrence, so a `RAISE` disposition raises on the second and later
occurrences as well as the first: a report reader wants bounded, readable output, while a
caller who asked to be stopped wants to be stopped, and a guard that disarms after firing
once is not a guard.

This SHALL be the rule for **every** once-per-stream code, not a per-code exception, so
that a future deduplicated code inherits an answer rather than the question. The
collector SHALL expose a way to evaluate a code's policy without recording an occurrence;
the deduplication bookkeeping itself lives with the emitter, which is what knows the scope
("this stream").

#### Scenario: policy / delivery matrix

| Case | Expected |
| --- | --- |
| Code → `IGNORE` | Count++; no retain/attach/log/callback/raise |
| Callback reads `reader.diagnostics` | Sees current event counted/retained; no lock held |
| Callback raises during `RAISE` | Callback error propagates; no replacement `DiagnosticRaisedError`; no `OnError.CONTINUE` |
| Callback starts op on same emitting reader | `UnsupportedOperationError` |

#### Scenario: deduplicated code policy matrix

| Case | Expected |
| --- | --- |
| Once-per-stream code, second qualifying occurrence, policy `COLLECT` | No second count, retention, log or callback |
| Once-per-stream code, second qualifying occurrence, policy `RAISE` | `DiagnosticRaisedError` raised again; still no second record |
| Once-per-stream code, second qualifying occurrence, policy `IGNORE` | Nothing happens |
| Escalation-only evaluation | Never appears in `retained`, never changes `counts`, never logs or calls back. The raised `DiagnosticRaisedError` still carries a full `Diagnostic` describing *this* occurrence — the caller being stopped should see the event that stopped them, not the first one |

### Requirement: Complete initial warning taxonomy

The initial `DiagnosticCode` set SHALL cover the library's advisory emissions via
the codes in the closed table above. Multiple call sites share a code only with the
same machine meaning; typed context distinguishes variants.

**No advisory SHALL be log-only.** Every condition the library reports as advice to
the caller SHALL be emitted through the central diagnostic path with a code, so it is
queryable on `reader.diagnostics` and escalatable by `DiagnosticPolicy`; the WARNING
log line is the projection of that emission, never a substitute for it.

That rule is a floor. The following two clauses are its ceiling, and a proposed code
SHALL satisfy both.

**Admission.** A `DiagnosticCode` SHALL report something the caller could not have
determined from the declared contract of the call, and can act on. It MAY describe a
property of the archive, or an outcome of the caller's request meeting the archive —
the archive-versus-caller distinction is descriptive and SHALL NOT be used as an
admission test. It SHALL NOT restate advice the API surface already carries.

**Placement.** When an operation returns a structured per-item report, that report
SHALL be the authoritative and sole carrier of per-item outcomes; the diagnostics
channel carries only facts with no return-value home. A fact SHALL have exactly one
authoritative channel. Where a fact would otherwise be reported twice, the return
value wins and the code SHALL NOT exist.

Where relocating a fact out of this channel would remove a caller's ability to be
stopped by it, the owning capability SHALL provide a named opt-in rather than
retaining the code for its escalation alone (see `safe-extraction`'s `AbortOn`).

No unused future codes reserved.

#### Scenario: taxonomy coverage

| Case | Expected |
| --- | --- |
| Advisory path that formerly logged only | Emits one of the initial codes through the central path |
| Member name containing a bidi formatting control | `MEMBER_NAME_BIDI_CONTROL` on the aggregate, not only a `logger.warning` |
| Proposed code restating a `CostReceipt` fact already returned at open time | Refused by the admission clause |
| Proposed code for a per-member outcome of an operation that returns a per-item report | Refused by the placement clause; the fact belongs in the result |
| A caller wants to be stopped by a fact that placement moved into a return value | A named opt-in on the owning capability, not a retained code |

### Requirement: Report unused explicit arguments as diagnostics

When an explicit argument is a **resource offered for use if needed** rather than an
assertion about the archive (see `archive-reading`), `open_archive()` SHALL accept it
and, when the resolved backend cannot act on it, SHALL emit one diagnostic naming the
argument. It MUST NOT raise.

| Condition | Code | `reason` |
| --- | --- | --- |
| Caller passed `encoding=` and `ReadBackend.USES_ENCODING` is `False` | `ENCODING_ARGUMENT_UNUSED` | why the backend decodes names another way |
| Caller passed any `password=` form and `ReadBackend.SUPPORTS_PASSWORD` is `False` | `PASSWORD_ARGUMENT_UNUSED` | that the format carries no encryption |

Each SHALL be emitted **at most once per `open_archive()` call**, before the reader is
returned, so a caller can inspect `reader.diagnostics` without listing anything.

An `encoding` value that came from detection's `encoding_hint` rather than from the
caller SHALL NOT emit — the caller asked for nothing.

`password=` SHALL behave identically in all three forms (a single value, a sequence of
candidates, a provider callable) on a format with no encryption: accepted, never
consulted, one diagnostic. A wrong password on an *encrypted* archive is unaffected and
still raises.

#### Scenario: unused argument matrix

| Case | Expected |
| --- | --- |
| `open_archive(iso, encoding="cp500")` | Opens; one `ENCODING_ARGUMENT_UNUSED`; names unchanged |
| `open_archive(zip, encoding="cp500")` | No diagnostic; the encoding is applied |
| Auto-detected encoding hint on a backend that ignores encoding | No diagnostic |
| `open_archive(tar, password="p")` / `password=["a","b"]` / `password=lambda r: "p"` | All three open; one `PASSWORD_ARGUMENT_UNUSED` each; no `UnsupportedOperationError` |
| Wrong password on an encrypted ZIP | Unchanged: `EncryptionError` |

### Requirement: Report an empty listing as a diagnostic

When a member listing completes **without error** and contains **zero members**, the
system SHALL emit `EMPTY_ARCHIVE` exactly once per reader, whatever the format. It MUST
NOT raise: an empty tar is *all zeros*, so no predicate over the bytes separates one from
a zero-filled junk file and any "zero members is an error" rule rejects a file `tar(1)`
itself produces.

The system SHALL NOT restrict the accepted lengths to the canonical ones either. GNU
tar's `-b` blocking factor makes **every** block-aligned zero length a legitimate empty
archive — `tar -b 64 -cf e.tar --files-from /dev/null` emits 32768 zero bytes,
byte-identical to a 32 KiB junk file and listed by `tar -tvf` without error. The two
sizes seen in practice are 1024 (Go `archive/tar`, hence the Docker/OCI empty layer) and
10240 (GNU tar default, Python `tarfile`); a rule admitting only those would emit a false
advisory against valid `-b` output while still being unable to refuse anything.
See `dev-docs/decisions/0015-zero-filled-files-are-valid-empty-tars.md`.

An incomplete listing (one published with an error) SHALL NOT emit it — the member count
is not the archive's.

On an empty listing the system SHALL additionally report **how the format was chosen**,
when that choice was not confirmed against the bytes:

| How the format was chosen | Additional code |
| --- | --- |
| Explicit `format=` argument, and detection now reports a different format or refuses the bytes | `EXPLICIT_FORMAT_LISTED_EMPTY` |
| Extension fallback (`FormatInfo.detected_by == "extension"`), i.e. no magic, probe or far-magic match | `EXTENSION_FORMAT_UNCONFIRMED` |
| Magic, content probe, far magic, or a directory path | none — the bytes confirmed the format |

`format=` SHALL remain an override: neither code refuses the open. The
`EXPLICIT_FORMAT_LISTED_EMPTY` re-detection SHALL run **only** on an empty listing, and
only when the source is a filesystem path, where reopening it cannot disturb the reader;
for a stream source the check is skipped rather than reaching into a live source's
position.

#### Scenario: empty listing matrix

| Case | Expected |
| --- | --- |
| `tar cf empty.tar --files-from /dev/null` | Opens, 0 members, `EMPTY_ARCHIVE`, no error |
| 32 KiB of zeros named `z.tar` | Opens, 0 members, `EMPTY_ARCHIVE` **and** `EXTENSION_FORMAT_UNCONFIRMED` |
| `open_archive(iso_path, format=TAR)` | Opens, 0 members, `EMPTY_ARCHIVE` **and** `EXPLICIT_FORMAT_LISTED_EMPTY` with `detected_format="ISO"` |
| `open_archive(tar_path, format=TAR)` on a real one-member tar | No diagnostic |
| Empty ZIP / empty 7z | `EMPTY_ARCHIVE`; no format code (magic confirmed the bytes) |
| A legitimately empty tar (all zeros, so no magic) | `EMPTY_ARCHIVE` **and** `EXTENSION_FORMAT_UNCONFIRMED` — truthful: the bytes really did not confirm it |
| Listing published with an error and zero members | No `EMPTY_ARCHIVE` |

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
| `MEMBER_NAME_NORMALIZED`, `MEMBER_NAME_ENCODING_INFERRED`, `MEMBER_NAME_BIDI_CONTROL`, `FORMAT_EXTENSION_CONFLICT`, `EXTENSION_FORMAT_UNCONFIRMED`, `SCAN_DIRECTORY_VANISHED`, `SCAN_ENTRY_VANISHED`, `ARCHIVE_EOF_MARKER_MISSING`, `ARCHIVE_TRAILING_DATA`, `MEMBER_TIMESTAMP_INVALID`, `SYMLINK_TARGET_UNAVAILABLE`, `DIGEST_UNVERIFIABLE`, `SEEK_INDEX_DEGRADED` | `EMPTY_ARCHIVE` (an empty archive is legitimate), `EXPLICIT_FORMAT_LISTED_EMPTY`, `ENCODING_ARGUMENT_UNUSED`, `PASSWORD_ARGUMENT_UNUSED`, `STREAM_REWIND_REDECOMPRESSES` |

Each exclusion is deliberate, and the reason SHALL be recorded so the boundary is not
rediscovered: `EMPTY_ARCHIVE` because an empty archive is legitimate and this spec
forbids treating zero members as an error; `ENCODING_ARGUMENT_UNUSED` and
`PASSWORD_ARGUMENT_UNUSED` because they report argument hygiene, and a pipeline that
speculatively passes a password to every call would otherwise raise on every
unencrypted archive; `EXPLICIT_FORMAT_LISTED_EMPTY` because `format=` is an override
and an override that halts the caller is not an override; and
`STREAM_REWIND_REDECOMPRESSES` because it reports the caller's access pattern rather
than the archive, and is most useful as a deliberately targeted tripwire.

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
