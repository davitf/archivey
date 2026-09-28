# diagnostics — fewer exception types

## MODIFIED Requirements

### Requirement: Complete per-code policy and delivery contract

The system SHALL provide a frozen `DiagnosticPolicy` with a default disposition
and immutable per-code overrides. The only dispositions SHALL be `IGNORE`,
`COLLECT`, and `RAISE` (no logger matching).

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

### Requirement: Report unused explicit arguments as diagnostics

When an explicit argument is a **resource offered for use if needed** rather than an
assertion about the archive (see `archive-reading`), `open_archive()` SHALL accept it
and, when the resolved backend cannot act on it, SHALL emit one diagnostic naming the
argument. It MUST NOT raise.

| Condition | Code | `reason` |
| --- | --- | --- |
| Caller passed `encoding=` and `ReadBackend.USES_ENCODING` is `False` | `ENCODING_ARGUMENT_UNUSED` | why the backend decodes names another way |
| Caller passed a concrete `password=` (a single value or a sequence) and `ReadBackend.SUPPORTS_PASSWORD` is `False` | `PASSWORD_ARGUMENT_UNUSED` | that the format carries no encryption |

Each SHALL be emitted **at most once per `open_archive()` call**, before the reader is
returned, so a caller can inspect `reader.diagnostics` without listing anything.

`password=` SHALL open identically in all three forms (a single value, a sequence of
candidates, a provider callable) on a format with no encryption: accepted, never
consulted. A single value or a sequence SHALL record one diagnostic. A provider callable
SHALL record none: it offers a password only if asked, and a format with no encryption
never asks, so no password was supplied. (The CLI passes a provider on every run.) A
wrong password on an *encrypted* archive is unaffected and still raises.

#### Scenario: unused argument matrix

| Case | Expected |
| --- | --- |
| `open_archive(sevenzip, encoding="cp500")` | Opens; one `ENCODING_ARGUMENT_UNUSED`; names unchanged |
| `open_archive(iso, encoding="cp500")` | No diagnostic; UTF-8 names unchanged, and the encoding applies to a Rock Ridge or plain name that is not valid UTF-8 |
| `open_archive(zip, encoding="cp500")` | No diagnostic; the encoding is applied |
| Auto-detected open with no `encoding=` on a backend that ignores encoding | No diagnostic |
| `open_archive(tar, password="p")` / `password=["a","b"]` | Both open; one `PASSWORD_ARGUMENT_UNUSED` each; no `ArchiveyUsageError` |
| `open_archive(tar \| gz \| directory, password=lambda r: "p")` | Opens; no `PASSWORD_ARGUMENT_UNUSED`; the provider is never called |
| Wrong password on an encrypted ZIP | Unchanged: `EncryptionError` |
