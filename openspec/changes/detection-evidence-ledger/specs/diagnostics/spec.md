## MODIFIED Requirements

### Requirement: A probe-only decode failure is filterable as unconfirmed

When a single-file decode fails and `ArchiveyError.format_unconfirmed` is true (see
`error-handling`), the system SHALL also emit a provenance-neutral unconfirmed-format
diagnostic so callers that filter diagnostics — without inspecting exception attributes —
see the same fact. The exception attribute and the diagnostic are two views of one
provenance; neither replaces the other.

`PROBE_FORMAT_UNCONFIRMED` SHALL be **renamed** (to `FORMAT_UNCONFIRMED_ON_DECODE`): the
event is "a decode failed on a format archivey guessed", and the flag now covers three
provenances, of which the probe is one. The **provenance** SHALL be carried in the
diagnostic's typed context — probe, filename, or an incomplete search — so a caller can
still filter for the probe case specifically.

The code SHALL NOT be a member of the `ARCHIVE_INTEGRITY_CODES` strict set: it is emitted
while stamping a typed `TruncatedError` / `CorruptionError`, and putting it in `strict` would
replace that typed error with `DiagnosticRaisedError`. Default disposition is COLLECT. When a
caller's policy resolves this code to RAISE (notably `DiagnosticPolicy.pedantic()`), the emit
SHALL surface the same typed error via `escalate_as` (carrying `format_unconfirmed=True`)
rather than `DiagnosticRaisedError`. The diagnostic is emitted at most once per reader.

That once-per-reader bound SHALL hold under **every** policy, RAISE included: an escalating
emit MUST NOT leave a second count, retention, log line or callback behind on a later
occurrence. The escalation obligation is met by the read path itself — every qualifying
occurrence re-raises the stamped typed error whether or not the diagnostic fires again — so
re-emitting would buy a duplicate record rather than a stop.

The **empty-listing** codes are unchanged, and the asymmetry is correct: an empty listing
raises nothing, so that channel is necessarily diagnostic-only. It is also narrower than it
looks — across 15 formats and two payloads the only empty listing produced was a zero-filled
`.tar`, because a zero-filled file is a structurally valid empty TAR; every container raised
instead.

#### Scenario: dual channel

| Case | Expected |
| --- | --- |
| Probe-only decode failure | `exc.format_unconfirmed is True` **and** the unconfirmed-format diagnostic, context provenance `probe` |
| Filename-only decode failure | Same, context provenance `name` |
| Decode failure on a structurally validated format | `exc.format_unconfirmed is False`; no diagnostic |
| `pedantic()`, probe-only decode failure | Same typed `TruncatedError`/`CorruptionError` with `format_unconfirmed=True` — not `DiagnosticRaisedError` |
| Three retried reads on one unconfirmed member | Diagnostic count stays 1; each exception still has `format_unconfirmed=True` |
| A caller filtering for probe-only failures | Filters on the context provenance, not on the code name |
