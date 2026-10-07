## MODIFIED Requirements

### Requirement: Detect truncated TAR archives

After full iteration, a missing or invalid TAR end marker SHALL emit
`ARCHIVE_EOF_MARKER_MISSING` on the reader operation aggregate and SHALL NOT
attach to `ArchiveInfo`, `CostReceipt`, or a member. Context SHALL be
`ArchiveEofContext(kind="archive_eof", format="tar",
expected_marker="two_zero_blocks", expected_bytes=1024, observed_bytes=...,
observed_kind=...)` plus best-effort archive display name. `observed_kind` SHALL
be `"absent"`, `"short"`, or `"nonzero"`; raw trailing bytes SHALL NOT be
retained. The damaged second trailer block (below) SHALL instead carry
`expected_marker="second_zero_block"`, `expected_bytes=512`, `observed_bytes=512`,
`observed_kind="nonzero"`, so that a whole listing is told from a shortened one by
the context: TAR's `"two_zero_blocks"` with `observed_kind="nonzero"` is always
escalated to `CorruptionError`.

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
  backend SHALL emit `ARCHIVE_EOF_MARKER_MISSING` with
  `expected_marker="second_zero_block"` and `observed_kind="nonzero"` under ordinary
  diagnostic disposition, with no escalation of its own: a warning by default,
  `DiagnosticRaisedError` after delivery when the code resolves to `RAISE` (as under
  `DiagnosticPolicy.strict()`), a count alone under `IGNORE`. GNU tar ("A lone zero
  block") and 7-Zip list the same archive with a warning. The backend SHALL tell this
  case from a rejected header by the error tarfile's last header parse raised, not by
  the bytes read, so it holds in streaming too. With no member before the zero block
  the non-null block stays `CorruptionError`, with `expected_marker="two_zero_blocks"`
  and a message that names that cause rather than a rejected header.
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
| Zero block, then a non-null block, after at least one member | both | `nonzero` (`expected_marker="second_zero_block"`) | `ARCHIVE_EOF_MARKER_MISSING`; every member listed and read; `extract_all` writes every member; trailing scan runs past the block | `DiagnosticRaisedError` after delivery |
| Zero block, then a non-null block, no member | both | `nonzero` | `CorruptionError` after delivery | `CorruptionError` after delivery |
| Rejected **final** header, nothing after | streaming | `absent` (limitation) | Warn; pass completes | `DiagnosticRaisedError` after delivery |
| Truncation inside member data / partial header | both | — | `TruncatedError` during iteration | `TruncatedError` during iteration |
| Corruption during `extract_all` | both | `nonzero` | Salvageable members written, then `CorruptionError` | same |
| Diagnostic code resolves to `IGNORE`, rejected header | both | `nonzero` | Count increments without delivery; `CorruptionError` raises | same |
| Diagnostic code resolves to `IGNORE`, `absent`/`short` | both | `absent`/`short` | Count increments without delivery; no error | — |
| Marker issue discovered after iteration | both | any | `reader.diagnostics` changes; frozen `ArchiveInfo` / `CostReceipt` unchanged | same |

### Requirement: Report non-zero bytes past the trailer

After a complete two-block null end-of-archive trailer, or after a damaged second
trailer block (a zero block, then a non-null one, after at least one member), the backend
SHALL scan the bytes that follow, up to 1 MiB past the trailer, whatever the
configuration. The first
non-zero byte in that window SHALL emit `ARCHIVE_TRAILING_DATA` under ordinary
diagnostic disposition, with no escalation of its own: a warning by default,
`DiagnosticRaisedError` after delivery when the code resolves to `RAISE` (as under
`DiagnosticPolicy.strict()`), a count alone under `IGNORE`.

The check SHALL run only after a complete two-block null trailer has been confirmed, or
after the damaged-second-block diagnostic has been emitted. It SHALL NOT run after an
`absent` or `short` trailer, or after a non-null block that is `CorruptionError`. After a
damaged second block it runs from the block after that one, because on a compressed tar
it is where the whole-stream checksum over the members already listed is usually
reached.

The 1 MiB bound is an effort limit, not a ceiling: past it the scan SHALL stop, SHALL
NOT report trailing data, and SHALL NOT refuse the archive. It is a module constant, not
a configuration field. On a compressed tar whose codec can carry a whole-stream checksum
(gzip, bzip2, xz, zstd, lz4, lzip, zlib), a scan that stops at the bound with the stream
not at its end SHALL emit `DIGEST_UNVERIFIABLE` (`reason="trailing_scan_limit"`): that
checksum was never checked. A tail that fails to decode — on a compressed tar, junk
after the compressed stream or a missing footer — SHALL end the scan with no error and,
except on bzip2 and xz (below), no diagnostic: every member was already read whole, and
that is not trailing tar data. A whole-stream checksum that fails in the scan (gzip
CRC-32 or ISIZE, zlib Adler-32, zstd or lz4 content checksum, lzip CRC-32) is not such a
tail: it covers the members already read, and SHALL raise `CorruptionError`. bzip2 and
xz check each block, and the last block's check can also be reached in the scan, but
those codecs report a failed check the same way as junk after the stream. On a
`.tar.bz2` or `.tar.xz` a tail that fails to decode SHALL therefore emit
`DIGEST_UNVERIFIABLE` (`reason="trailing_decode_failed"`) instead of ending the scan
silently. Where that check is reached depends on how far the codec has read ahead
when the last member's bytes are delivered, not on the archive: when it is reached
during the member read, the read SHALL raise `CorruptionError` instead. Either way the
failure SHALL NOT pass silently.

The code is not a truncation: nothing is truncated, the file is *longer* than the
listing accounts for.

#### Scenario: trailing-bytes matrix

| Case | Default policy | `DiagnosticPolicy.strict()` |
| --- | --- | --- |
| Valid tar, trailer, EOF | No diagnostic | No diagnostic |
| Valid tar + 4 KiB of zeros (`tar` pads to 10 KiB records) | No diagnostic | No diagnostic |
| Valid tar + 4 KiB of `b"JUNK"` | `ARCHIVE_TRAILING_DATA` | `DiagnosticRaisedError` |
| Damaged second trailer block, then junk | `ARCHIVE_EOF_MARKER_MISSING` (`"second_zero_block"`), then `ARCHIVE_TRAILING_DATA` | `DiagnosticRaisedError` |
| `.tar.gz` with a bad CRC-32 and a damaged second trailer block | `CorruptionError` | `DiagnosticRaisedError` (the marker diagnostic is raised first) |
| Valid tar + zeros + one non-zero byte, within 1 MiB | `ARCHIVE_TRAILING_DATA` | `DiagnosticRaisedError` |
| First non-zero byte more than 1 MiB past the trailer | No diagnostic; the scan stopped (a compressed tar: `DIGEST_UNVERIFIABLE`) | No diagnostic (a compressed tar: raises on `DIGEST_UNVERIFIABLE`) |
| Two tars concatenated | `ARCHIVE_TRAILING_DATA`; the first is listed | `DiagnosticRaisedError` |
| Legitimately empty tar (10240 zeros), or 32 KiB of zeros | No diagnostic; all zeros | No diagnostic |
| A real ISO opened as TAR | Empty listing plus `ARCHIVE_TRAILING_DATA`: its zeros stop at 32768 | Raises |
| Missing / short trailer | `ARCHIVE_EOF_MARKER_MISSING`; the scan does not run | Raises on that code |
| `.tar.gz`, junk inside the gzip stream after the trailer | Tail decompressed, at most 1 MiB; `ARCHIVE_TRAILING_DATA` | `DiagnosticRaisedError` |
| `.tar.gz`, junk after the gzip stream or a missing gzip footer | No diagnostic, no error | No diagnostic, no error |
| `.tar.gz` / `.tar.zst` / `.tar.lz4`, a member byte damaged, stream checksum reached within 1 MiB of the trailer | `CorruptionError` | `CorruptionError` |
| `.tar.bz2` / `.tar.xz`, the last block's check failing, reached in the trailing scan (within 1 MiB of the trailer), or junk after the stream | `DIGEST_UNVERIFIABLE` | Raises on `DIGEST_UNVERIFIABLE` |
| `.tar.bz2` / `.tar.xz`, the same failing check reached while the last member is read (decided by the codec's read-ahead, so by member size) | `CorruptionError` from the read | `CorruptionError` from the read |
