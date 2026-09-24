## ADDED Requirements

### Requirement: Document TAR EOF honesty and its diagnostic codes

End-user documentation SHALL state that:

- Stdlib-backed TAR may silently shorten a listing when a member header after the
  first is corrupt; archivey’s backstop is its end-of-archive check.
- By default archivey raises `CorruptionError` when the stopped scan lands on a
  rejected (non-null) header block — which a well-formed tar never produces. A merely
  trailer-less or `cat`-joined tar (ended cleanly on a member boundary) is warned about
  via `ARCHIVE_EOF_MARKER_MISSING`, not raised, because it is indistinguishable from a
  tar truncated at a member boundary.
- Setting `ARCHIVE_EOF_MARKER_MISSING` to `RAISE` (or using
  `DiagnosticPolicy.strict()`) makes that ambiguous residual raise
  `DiagnosticRaisedError`, for inventory / dedupe / validators that need a provably
  complete listing. Docs SHALL NOT describe this as "the only way archivey catches
  corruption" — a rejected header is caught by default.
- A non-zero byte after the trailer is reported as `ARCHIVE_TRAILING_DATA` only within
  1 MiB of it; docs SHALL state the bound, and that zero padding passes.
- **Streaming limitation:** a corrupt header as the archive's *final* block is caught in
  random-access reads but NOT in forward-only streaming (it surfaces as a missing-trailer
  warning there). Docs SHALL state this and that a future native TAR reader may close the
  gap (post-v1), without promising a release.

This SHALL appear in the formats guide and in the user-facing Gotchas page. Internal
threat-model / open-issues material MUST NOT be the only place this is written.

#### Scenario: TAR EOF documentation matrix

| Case | Expected |
| --- | --- |
| Reader opens formats / Gotchas for TAR quirks | Finds silent-shorten + diagnostic + the rejected-header-raises-by-default vs missing-trailer-warns split |
| Inventory / dedupe guidance | Shows a `RAISE` disposition on `ARCHIVE_EOF_MARKER_MISSING` as the escalation for the ambiguous residual, not as the only corruption backstop |
| Trailing data | Documented as reported within 1 MiB of the trailer, and not beyond |
| Streaming final-header limitation | Documented as caught in random access, missed in streaming; native TAR may close it later |
| Post-v1 native TAR | Mentioned as possible future improvement for the residual + streaming gap, not a v1 promise |

## REMOVED Requirements

### Requirement: Document TAR EOF honesty and the strict_archive_eof opt-in

**Reason**: The opt-in it documents is removed. Replaced by *Document TAR EOF honesty and
its diagnostic codes*.
**Migration**: None for callers; the docs move with the code.
