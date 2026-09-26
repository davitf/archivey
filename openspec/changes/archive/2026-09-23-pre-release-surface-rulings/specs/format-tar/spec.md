## ADDED Requirements

### Requirement: Report non-zero bytes past the trailer

After a complete two-block null end-of-archive trailer, the backend SHALL scan the bytes
that follow, up to 1 MiB past the trailer, whatever the configuration. The first
non-zero byte in that window SHALL emit `ARCHIVE_TRAILING_DATA` under ordinary
diagnostic disposition, with no escalation of its own: a warning by default,
`DiagnosticRaisedError` after delivery when the code resolves to `RAISE` (as under
`DiagnosticPolicy.strict()`), a count alone under `IGNORE`.

The check SHALL run only on the success path of the trailer verification — after a
complete two-block null trailer has been confirmed — so it never competes with the
`absent` / `short` / `nonzero` classifications of the trailer itself.

The 1 MiB bound is an effort limit, not a ceiling: past it the scan SHALL stop and report
nothing, and SHALL NOT refuse the archive. It is a module constant, not a configuration
field. A tail that fails to decode — on a compressed tar, junk after the compressed
stream or a missing footer — SHALL end the scan with no error and no diagnostic: every
member was already read whole, and that is not trailing tar data.

The code is not a truncation: nothing is truncated, the file is *longer* than the
listing accounts for.

#### Scenario: trailing-bytes matrix

| Case | Default policy | `DiagnosticPolicy.strict()` |
| --- | --- | --- |
| Valid tar, trailer, EOF | No diagnostic | No diagnostic |
| Valid tar + 4 KiB of zeros (`tar` pads to 10 KiB records) | No diagnostic | No diagnostic |
| Valid tar + 4 KiB of `b"JUNK"` | `ARCHIVE_TRAILING_DATA` | `DiagnosticRaisedError` |
| Valid tar + zeros + one non-zero byte, within 1 MiB | `ARCHIVE_TRAILING_DATA` | `DiagnosticRaisedError` |
| First non-zero byte more than 1 MiB past the trailer | No diagnostic; the scan stopped | No diagnostic |
| Two tars concatenated | `ARCHIVE_TRAILING_DATA`; the first is listed | `DiagnosticRaisedError` |
| Legitimately empty tar (10240 zeros), or 32 KiB of zeros | No diagnostic; all zeros | No diagnostic |
| A real ISO opened as TAR | Empty listing plus `ARCHIVE_TRAILING_DATA`: its zeros stop at 32768 | Raises |
| Missing / short trailer | `ARCHIVE_EOF_MARKER_MISSING`; the scan does not run | Raises on that code |
| `.tar.gz`, junk inside the gzip stream after the trailer | Tail decompressed, at most 1 MiB; `ARCHIVE_TRAILING_DATA` | `DiagnosticRaisedError` |
| `.tar.gz`, junk after the gzip stream or a missing gzip footer | No diagnostic, no error | No diagnostic, no error |

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
    block following tarfile's stop being full and non-null. A rejected **final** header
    (no data after it) is NOT detectable this way and surfaces as a missing trailer
    instead — see the streaming limitation below.
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
- `extract_all` on **random access fails closed** — extract-prep materializes the member
  list (complete-or-raise) before writing, so a corrupt/truncated archive raises before any
  member is written and leaves no partial output. **Streaming** `extract_all` verifies at the
  end of the forward pass, so it writes the salvageable members first and then raises.

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
| Rejected **final** header, nothing after | streaming | `absent` (limitation) | Warn; pass completes | `DiagnosticRaisedError` after delivery |
| Truncation inside member data / partial header | both | — | `TruncatedError` during iteration | `TruncatedError` during iteration |
| Corruption during `extract_all` | random-access | `nonzero` | Fails closed: raises before any write (no partial output) | same |
| Corruption during `extract_all` | streaming | `nonzero` | Salvageable members written, then `CorruptionError` | same |
| Diagnostic code resolves to `IGNORE`, rejected header | both | `nonzero` | Count increments without delivery; `CorruptionError` raises | same |
| Diagnostic code resolves to `IGNORE`, `absent`/`short` | both | `absent`/`short` | Count increments without delivery; no error | — |
| Marker issue discovered after iteration | both | any | `reader.diagnostics` changes; frozen `ArchiveInfo` / `CostReceipt` unchanged | same |

## REMOVED Requirements

### Requirement: Under strict EOF, nothing but zeros may follow the trailer

**Reason**: `strict_archive_eof` is removed (davitf, #315 S21-K8 thread, 2026-09-20). The
trailing-bytes scan it gated now always runs, bounded, and reports under the ordinary
diagnostic policy.
**Migration**: See *Report non-zero bytes past the trailer*. A caller who set the flag sets
`ARCHIVE_TRAILING_DATA` and `ARCHIVE_EOF_MARKER_MISSING` to `RAISE`, or uses
`DiagnosticPolicy.strict()`, and catches `DiagnosticRaisedError`.
