## MODIFIED Requirements

### Requirement: Complete per-code policy and delivery contract

The system SHALL provide a frozen `DiagnosticPolicy` with two keyword-only code sets,
`ignore` and `raise_on`. A code in `raise_on` resolves to `RAISE`, a code in `ignore` to
`IGNORE`, and every other code to `COLLECT`. The only dispositions SHALL be `IGNORE`,
`COLLECT`, and `RAISE` (no logger matching).

```python
DiagnosticPolicy(*, ignore: Collection[DiagnosticCode] = frozenset(),
                 raise_on: Collection[DiagnosticCode] = frozenset())
```

Each argument SHALL accept any iterable of codes, a code's name or value as a string
included, and the field SHALL hold a `frozenset` of members, so a caller builds a policy
with set operations on the public code sets. A code in both sets SHALL raise
`ArchiveyUsageError`: keyword arguments have no order, so neither can win, and keeping
either silently drops what the other asked for. A bare string SHALL raise
`ArchiveyUsageError` rather than be read as a set of characters. There SHALL be no
default disposition other than `COLLECT`: "raise on everything but X" is
`raise_on=frozenset(DiagnosticCode) - {X}`.

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
Callbacks MAY read snapshots; same-emitting-reader/stream operational reentry is
rejected: the reader's operation gate raises `ArchiveyUsageError` (reader-concurrency),
and a re-entrant call that gets as far as emitting a diagnostic of its own raises
`ArchiveyUsageError` from the collector; other readers OK.

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

**An operation that cannot unwind raises once, when it can.** A stream operation that
emits from inside a decode or an index scan (a read, seek or size query on a
decompressing stream) SHALL hold what an emit or an escalation-only evaluation would
raise, a callback's exception included, and raise the first error it held once its own
state is consistent. Each occurrence is still evaluated and delivered as above. The
operation raises at most once: a later occurrence in the same operation that would also
raise is evaluated, and delivered if it is recorded, but not raised. The next operation
evaluates afresh.

#### Scenario: policy / delivery matrix

| Case | Expected |
| --- | --- |
| Code → `IGNORE` | Count++; no retain/attach/log/callback/raise |
| Same code in `ignore` and `raise_on` | `ArchiveyUsageError` at construction |
| `ignore="archive_trailing_data"` (bare string) | `ArchiveyUsageError` at construction |
| `raise_on=ARCHIVE_INTEGRITY_CODES - {ARCHIVE_TRAILING_DATA}` | `STRICT` except that code, which is collected |
| Callback reads `reader.diagnostics` | Sees current event counted/retained; no lock held |
| Callback raises during `RAISE` | Callback error propagates; no replacement `DiagnosticRaisedError`; no `OnError.CONTINUE` |
| Callback starts op on same emitting reader | `ArchiveyUsageError` from the reader's operation gate |

#### Scenario: deduplicated code policy matrix

| Case | Expected |
| --- | --- |
| Once-per-stream code, second qualifying occurrence, policy `COLLECT` | No second count, retention, log or callback |
| Once-per-stream code, second qualifying occurrence, policy `RAISE` | `DiagnosticRaisedError` raised again; still no second record |
| Once-per-stream code, second qualifying occurrence, policy `IGNORE` | Nothing happens |
| Two occurrences inside one deferring stream operation, policy `RAISE` | The operation raises once, with the first error held; the second is delivered if recorded, not raised |
| Escalation-only evaluation | Never appears in `retained`, never changes `counts`, never logs or calls back. The raised `DiagnosticRaisedError` still carries a full `Diagnostic` describing *this* occurrence — the caller being stopped should see the event that stopped them, not the first one |

### Requirement: Named diagnostic policy presets and taxonomy-growth contract

The system SHALL provide named `DiagnosticPolicy` instances, as class attributes, so a
caller can express a coarse strictness without enumerating the taxonomy:

```python
ARCHIVE_INTEGRITY_CODES: frozenset[DiagnosticCode]

DiagnosticPolicy.STRICT    # DiagnosticPolicy(raise_on=ARCHIVE_INTEGRITY_CODES)
DiagnosticPolicy.PEDANTIC  # DiagnosticPolicy(raise_on=frozenset(DiagnosticCode))
```

`ARCHIVE_INTEGRITY_CODES` SHALL be a public frozen set covering the codes that report
the archive's own bytes or metadata as anomalous:

| In `ARCHIVE_INTEGRITY_CODES` | Excluded |
| --- | --- |
| `MEMBER_NAME_NORMALIZED`, `MEMBER_NAME_ENCODING_INFERRED`, `MEMBER_NAME_BIDI_CONTROL`, `FORMAT_EXTENSION_CONFLICT`, `EXTENSION_FORMAT_UNCONFIRMED`, `SCAN_DIRECTORY_VANISHED`, `SCAN_ENTRY_VANISHED`, `ARCHIVE_EOF_MARKER_MISSING`, `ARCHIVE_TRAILING_DATA`, `MEMBER_TIMESTAMP_INVALID`, `MEMBER_HEADER_RECORD_SKIPPED`, `SYMLINK_TARGET_UNAVAILABLE`, `DIGEST_UNVERIFIABLE`, `SEEK_INDEX_DEGRADED` | `EMPTY_ARCHIVE` (an empty archive is legitimate), `EXPLICIT_FORMAT_LISTED_EMPTY`, `ENCODING_ARGUMENT_UNUSED`, `PASSWORD_ARGUMENT_UNUSED`, `STREAM_REWIND_REDECOMPRESSES`, `PROBE_FORMAT_UNCONFIRMED`, `ENCRYPTED_MEMBER_UNVERIFIED` |

Each exclusion is deliberate, and the reason SHALL be recorded so the boundary is not
rediscovered: `EMPTY_ARCHIVE` because an empty archive is legitimate and this spec
forbids treating zero members as an error; `ENCODING_ARGUMENT_UNUSED` and
`PASSWORD_ARGUMENT_UNUSED` because they report argument hygiene, and a pipeline that
speculatively passes a password to every call would otherwise raise on every
unencrypted archive; `EXPLICIT_FORMAT_LISTED_EMPTY` because `format=` is an override
and an override that halts the caller is not an override;
`STREAM_REWIND_REDECOMPRESSES` because it reports the caller's access pattern rather
than the archive, and is most useful as a deliberately targeted tripwire;
`ENCRYPTED_MEMBER_UNVERIFIED` because the trigger is the caller abandoning the stream
before EOF (extract never fires it), and putting it in `STRICT` would turn a ZipCrypto
peek into `DiagnosticRaisedError` (revisit when `stream.verified` lands and this code
is retired); and
`PROBE_FORMAT_UNCONFIRMED` because a probe-only identification is an advisory about
what the file *is* (its bytes did pass that format's content check), not a finding
about the archive's own bytes, and the code only ever accompanies a read that has
already failed with a typed error, so `STRICT` would have nothing further to stop.
(The emit keeps that typed error through `escalate_as` when a policy does resolve the
code to RAISE, so the exclusion is not what protects it.)
`EXTENSION_FORMAT_UNCONFIRMED`, its sibling, is **in** the set because it also fires
on a successful open: an extension-only empty listing, where no byte confirmed the
format at all, is exactly the case a `STRICT` caller wants stopped.

Presets SHALL be ordinary frozen `DiagnosticPolicy` values — no new resolution axis,
and no field on `Diagnostic`. A caller adjusts a preset by building another policy from
the same sets, for example
`DiagnosticPolicy(raise_on=ARCHIVE_INTEGRITY_CODES, ignore={PASSWORD_ARGUMENT_UNUSED})`.

**Taxonomy growth.** New `DiagnosticCode` members MAY be added in minor releases.
`PEDANTIC`, and any policy built from `frozenset(DiagnosticCode)`, takes every code of
the installed version, and therefore SHALL NOT be described as version-stable: a
caller running it starts raising on events their working program never produced. The
documentation SHALL state this, and SHALL present `STRICT` — whose membership is
versioned alongside the taxonomy — as the recommended strict mode. Removing a code
remains a breaking change.

#### Scenario: preset matrix

| Case | Expected |
| --- | --- |
| `STRICT`, archive with a truncated TAR trailer | `DiagnosticRaisedError` on `ARCHIVE_EOF_MARKER_MISSING` |
| `STRICT`, unencrypted archive opened with `password=` | No raise; `PASSWORD_ARGUMENT_UNUSED` is collected |
| `PEDANTIC`, same call | `DiagnosticRaisedError` on `PASSWORD_ARGUMENT_UNUSED` |
| `STRICT`, legitimately empty tar | No raise; `EMPTY_ARCHIVE` collected |
| Preset value compared to an equivalent hand-built policy | Equal; presets add no resolution axis |
| A new code added in a later minor release | `STRICT` membership is explicit; `PEDANTIC` silently gains it |
| `ENCRYPTED_MEMBER_UNVERIFIED in ARCHIVE_INTEGRITY_CODES` | False |
| `STRICT`, ZipCrypto member, `read(1)`, close | No raise; `ENCRYPTED_MEMBER_UNVERIFIED` collected |
| `PEDANTIC`, same call | `DiagnosticRaisedError` |

#### Scenario: probe code stays out of strict

| Case | Expected |
| --- | --- |
| `PROBE_FORMAT_UNCONFIRMED in ARCHIVE_INTEGRITY_CODES` | False |
| `DiagnosticPolicy.STRICT` disposition for that code | COLLECT (in neither set) |

#### Scenario: Strictness keeps the refuse-the-archive behaviour a lenient parse gives up

- **GIVEN** a backend that drops a malformed optional member-header record and lists the
  member, emitting `MEMBER_HEADER_RECORD_SKIPPED`
- **WHEN** the caller passes `DiagnosticPolicy.STRICT`
- **THEN** the listing SHALL raise, because the code is in `ARCHIVE_INTEGRITY_CODES`
- **AND** this is why a backend MAY become lenient about such a record without removing
  the strict outcome: leniency moves the default, the policy keeps the choice
