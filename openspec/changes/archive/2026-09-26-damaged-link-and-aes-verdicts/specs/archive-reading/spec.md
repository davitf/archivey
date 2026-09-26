# archive-reading — damaged link targets delta

## ADDED Requirements

### Requirement: A damaged data-stored link target leaves the listing intact

When a backend reads a symlink's target from the member's data (ZIP, 7z, RAR3/4) and
that read raises `CorruptionError` or `TruncatedError` (a CRC or HMAC mismatch, a
decompressor failure, data past the declared size, data the file cuts short), link
finalization SHALL NOT raise it. The link SHALL stay listed with its type and
`link_target` unset, the other links SHALL still be resolved, and
`SYMLINK_TARGET_UNAVAILABLE` SHALL be emitted with `reason="target_data_damaged"` and a
message naming the fault. The member SHALL NOT be memoized as resolved: opening the link,
following it, or extracting it SHALL read the target again and raise the fault itself,
and extraction SHALL record that link as a per-member failure. `SYMLINK_TARGET_UNAVAILABLE`
is in `ARCHIVE_INTEGRITY_CODES`, so `DiagnosticPolicy.strict()` refuses the archive.
This holds in random access and at the end of a streaming pass alike.

#### Scenario: damaged link target matrix

| Case | Expected |
| --- | --- |
| ZIP symlink whose stored data fails its CRC, `members()` | Every member listed; link `link_target is None`; `SYMLINK_TARGET_UNAVAILABLE`, `reason="target_data_damaged"` |
| Same, `open()` on the link | `CorruptionError` |
| Same, `extract_all(on_error=CONTINUE)` | Link `FAILED` with `CorruptionError`; other members extract |
| Same, `DiagnosticPolicy.strict()` | Listing raises `DiagnosticRaisedError` |
| WinZip AES symlink with a failing HMAC, one password or several | Listed targetless with `reason="target_data_damaged"` |
| 7z symlink whose data fails its CRC | Listed targetless with `reason="target_data_damaged"` |
| ZIP symlink whose data outruns its declared size | Listed targetless; the message names the declared size |
