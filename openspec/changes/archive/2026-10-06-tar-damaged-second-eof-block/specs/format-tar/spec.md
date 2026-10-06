## MODIFIED Requirements

### Requirement: Detect truncated TAR archives

After full iteration, a missing or invalid TAR end marker SHALL emit
`ARCHIVE_EOF_MARKER_MISSING` on the reader operation aggregate and SHALL NOT
attach to `ArchiveInfo`, `CostReceipt`, or a member. Context SHALL be
`ArchiveEofContext(kind="archive_eof", format="tar",
expected_marker="two_zero_blocks", expected_bytes=1024, observed_bytes=...,
observed_kind=...)` plus best-effort archive display name. `observed_kind` SHALL
be `"absent"`, `"short"`, or `"nonzero"`; raw trailing bytes SHALL NOT be
retained.

Stdlib `tarfile` does not report
*why* it stopped iterating (a real trailer, a corrupt non-first header treated as
clean EOF, or exhausted data all return the same result), so the backend SHALL
classify the end-of-archive from the block tarfile stopped on rather than from a
single monolithic flag:

- **Rejected header → `CorruptionError`, whatever the diagnostic policy.** When a
  full non-null 512-byte block sits where the next header / end marker was expected,
  tarfile rejected it as a header — the detectable slice of "corrupt member header
  after the first = clean end of archive," a silently shortened listing. A conformant,
  complete tar never produces this (its two-or-more null trailer blocks end the scan
  first). Emitted with `observed_kind="nonzero"` after the diagnostic's normal
  count/retention/log/callback ordering, then escalated to `CorruptionError`.
  - In **random-access** mode the backend SHALL detect this via a read probe
    (`_EofProbeStream`): after the header scan it inspects the block tarfile's final
    header attempt returned (``TarFile.next()`` always tries one more block before
    stopping) and treats a full non-null block there as a rejected header. This catches
    the case even when the bad header is the archive's **final** block (nothing
    following), including after a GNU sparse member whose logical ``size`` does not
    match the physical packed end. It SHALL NOT key the decision on
    ``offset_data + roundup(size)`` (that formula is wrong for sparse). When the probe
    is unavailable it SHALL fall back to the trailing-block check.
  - In **streaming** mode (no probe) the backend SHALL detect a rejected header via the
    block following tarfile's stop being full and non-null, when tarfile did not stop
    on a zero block (below). A rejected **final** header
    (no data after it) is NOT detectable this way and surfaces as a missing trailer
    instead — see the streaming limitation below.
- **Damaged second trailer block → ordinary diagnostic.** When tarfile stopped on a
  zero block (the first trailer block) after at least one member, and the block after
  it is full and non-null, the listing is whole and only the end-of-archive marker is
  damaged. Every member SHALL be listed and readable in both access modes, and the
  backend SHALL emit `ARCHIVE_EOF_MARKER_MISSING` with `observed_kind="nonzero"` under
  ordinary diagnostic disposition, with no escalation of its own: a warning by default,
  `DiagnosticRaisedError` after delivery when the code resolves to `RAISE` (as under
  `DiagnosticPolicy.strict()`), a count alone under `IGNORE`. GNU tar ("A lone zero
  block") and 7-Zip list the same archive with a warning. The backend SHALL tell this
  case from a rejected header by the error tarfile's last header parse raised, not by
  the bytes read, so it holds in streaming too. With no member before the zero block
  the non-null block stays `CorruptionError`.
- **Missing / short trailer → ordinary diagnostic.** A stream that ended cleanly on a member
  boundary with no valid two-block trailer (`observed_kind="absent"` for EOF,
  `"short"` for a partial block) is the irreducibly ambiguous residual: a
  complete-but-trailer-less tar and a tar truncated exactly at a member boundary are
  byte-identical and not decidable without a native TAR header walker (post-v1). It
  SHALL follow ordinary diagnostic disposition with no escalation of its own: a warning
  by default, `DiagnosticRaisedError` after delivery when the code resolves to `RAISE`
  (as under `DiagnosticPolicy.strict()`), a count alone under `IGNORE`.

The rejected-header escalation to `CorruptionError` SHALL take precedence over
`DiagnosticRaisedError`, including when the diagnostic disposition is `IGNORE` or
`RAISE`. Logging-handler or callback exceptions propagate at their earlier ordered step.

The archive-level EOF check runs at the end of the member scan, so its escalation is a
terminal listing error carried through the `partial-members-and-errors` report model:

- `members()` / `scan_members()` are complete-or-raise — they raise the stored escalation.
- `members_report()` (and `members_report_if_available()`) return the recovered prefix plus
  the terminal `error`, so a caller can still inspect the salvageable members.
- `__iter__` (both access modes) yields the recovered members, then raises.
- `extract_all` runs one forward pass in **both** access modes: random access enforces the
  listing limits as members arrive instead of listing first, so a compressed tar is
  decoded once. The EOF check therefore runs at the end of that pass: the salvageable
  members are written first, and then the call raises. It does not fail closed.

The check SHALL raise the escalation from the member scan (so the report model records it as
`error`); it SHALL NOT record the archive-level EOF only on a separate report field.

Truncation *inside* a member's data or across a partial header block is out of scope of
this end-of-marker check: it already raises `TruncatedError` **during iteration** (stdlib
`tarfile` raises `ReadError: unexpected end of data`, translated by the backend),
whatever the diagnostic policy, in both random-access and streaming modes.

**Streaming limitation (known):** stdlib `tarfile`'s streaming `_Stream` hides its
header reads, so the random-access offset probe is unavailable and a rejected **final**
header (a corrupt header as the archive's last block, nothing following) is misclassified
as `observed_kind="absent"` — treated as a missing trailer (warn by default,
`DiagnosticRaisedError` under `RAISE`) rather than `CorruptionError`. Random access catches this
case. A native TAR walker (post-v1) that validates each header at its offset would close
the gap for streaming too. The system SHALL NOT claim otherwise.

#### Scenario: TAR EOF matrix

| Case | Mode | `observed_kind` | Default policy | Code set to `RAISE` |
| --- | --- | --- | --- | --- |
| Valid two-block null marker (incl. minimal `tar -b1`, trailing record padding) | both | — (OK) | No diagnostic or error | No diagnostic or error |
| Missing marker / truncated at member boundary | both | `absent` | `ARCHIVE_EOF_MARKER_MISSING`; pass completes | `DiagnosticRaisedError` after delivery |
| Partial trailing block | both | `short` | Warn as above; pass completes | `DiagnosticRaisedError` after delivery |
| Rejected non-first header, data follows | both | `nonzero` | `CorruptionError` after delivery | `CorruptionError` after delivery |
| Rejected **final** header, nothing after | random-access | `nonzero` (via probe) | `CorruptionError` after delivery | `CorruptionError` after delivery |
| Zero block, then a non-null block, after at least one member | both | `nonzero` | `ARCHIVE_EOF_MARKER_MISSING`; every member listed and read; `extract_all` writes every member | `DiagnosticRaisedError` after delivery |
| Rejected **final** header, nothing after | streaming | `absent` (limitation) | Warn; pass completes | `DiagnosticRaisedError` after delivery |
| Truncation inside member data / partial header | both | — | `TruncatedError` during iteration | `TruncatedError` during iteration |
| Corruption during `extract_all` | both | `nonzero` | Salvageable members written, then `CorruptionError` | same |
| Diagnostic code resolves to `IGNORE`, rejected header | both | `nonzero` | Count increments without delivery; `CorruptionError` raises | same |
| Diagnostic code resolves to `IGNORE`, `absent`/`short` | both | `absent`/`short` | Count increments without delivery; no error | — |
| Marker issue discovered after iteration | both | any | `reader.diagnostics` changes; frozen `ArchiveInfo` / `CostReceipt` unchanged | same |
