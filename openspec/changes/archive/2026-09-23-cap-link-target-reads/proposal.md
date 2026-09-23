# Cap symlink targets read from member data

## Why

ZIP, 7z and RAR3/4 store a symlink's target as the member's *data*, and listing reads it
(`_ensure_link_target`). ZIP and 7z compress that data, and the read was a bare
`read()`: a 398 KiB ZIP whose one symlink "target" was 400 MiB of zeros peaked at
2 400 MiB inside `members()`, with `max_members=10` and `max_metadata_bytes=4096` both set.
The caller had not opened a member.

The listing limits did not see it either. `max_metadata_bytes` weighs `link_target`, as
§"Listing metadata-byte accounting" requires, but it weighs it when the member is
registered, and a data-stored target is read after every member is registered. So the
field was weighed as `None`: 200 targets of 20 KB each registered as 2 000 bytes.

Recorded as blocking finding S21-K10 in the sweep. The maintainer ruled the cap and its
behaviour on 2026-09-23: 4096 bytes, and a larger target is rejected as corrupt or
malicious rather than truncated, through the diagnostic policy.

## What changes

- A data-stored symlink target longer than 4096 bytes (`MAX_LINK_TARGET_BYTES`) is left
  unset and reported as `SYMLINK_TARGET_UNAVAILABLE` with `reason="target_too_long"`. It
  is never truncated.
- The read is bounded: a member whose declared size is over the cap is not opened. ZIP and
  7z verify data against the declared size, so data that outruns a smaller one fails as
  `CorruptionError` there, as for any member; a read with no declared size stops at
  cap + 1 bytes.
- A Windows reparse buffer is read as far as its own header declares (8 bytes, then the
  payload length it states, at most 0xFFFF) and is never refused for size, because a
  reparse-flagged member can be an ordinary file whose content is kept. The target a
  buffer yields is held to the same 4096-byte cap, in UTF-8.
- A target resolved after registration is added to the listing tracker as it is
  resolved, under the same enforcement as registration, so `max_metadata_bytes` covers
  it.

The code is an archive-integrity code already, so `DiagnosticPolicy.strict()` refuses such
an archive; the default policy lists every member and fails only that link at extraction,
as it does for a target that is encrypted.

## Impact

- Affected specs: `archive-reading` (a new requirement for the cap; the metadata-accounting
  requirement gains the late-resolved targets).
- Targets stored in a header (TAR `linkname`, RAR5 redirection records, Rock Ridge) are
  not read through the cap. They are already allocated by the header parser and weighed at
  registration.
- A POSIX symlink cannot carry a target over 4095 bytes, so no archive written from a
  Linux or macOS filesystem changes behaviour. A Windows symlink with a target over 4096
  UTF-8 bytes would now be refused; the maintainer's ruling covers it as out of range.
