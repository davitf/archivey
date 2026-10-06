# Design

## Decisions

### A later version-0 member is a member, not trailing data

The lzip manual allows trailing data after the last member, and the forward decoder
ends the data at bytes that do not start with `LZIP`. A full `LZIP` magic after a
member always starts a member, whatever its version byte, so a version-0 member after
a version-1 one is refused as unsupported rather than read past. Reading it past would
drop the member from the size and from what a read returns, and the result would look
like a complete file.

`lzip -d` 1.24.1 agrees. It was run on a version-0 member alone, first, after a
version-1 member, and with a real 12-byte version-0 trailer. On each layout it printed
the members before the version-0 one, then "Version 0 member format not supported.",
and exited 2.

### The walk does not scan backwards for `LZIP\x00`

A version-0 trailer is 12 bytes, with no member size, so the backward walk cannot
find the start of a version-0 member from its end. To refuse one strictly between two
version-1 members directly, the walk would have to scan back for the bytes
`LZIP\x00`. Those bytes can occur inside LZMA data, so the scan would be a guess, not
a check. A false match would make the walk index wrong member starts or refuse a
readable file. Rejected.

### A walk that fails as corrupt degrades to the sequential read

Where the walk cannot reach a version-0 member, strictly between two version-1
members, it reads the end of that member's LZMA data and its 12-byte trailer as a
20-byte trailer. The member size it finds there does not lead back to a header, and the
walk raises `CorruptionError`. `build_index_backwards` catches `CorruptionError`,
reports `SEEK_INDEX_DEGRADED` and returns no index, so a seek falls back to the
sequential read, and the forward decoder refuses the member with
`UnsupportedFeatureError`. The caller-visible guarantee holds, a seek never skips a
member the forward read refuses, even though the walk's own error is corruption.
`test_lzip_seek_past_a_version_0_member_is_unsupported[middle_real]` pins this path:
if `build_index_backwards` stopped catching `CorruptionError`, it would fail.

The walk refuses a version-0 member directly where it does reach one: at a member
start, at the start of its range (`stop_at`, which covers one at the start of the file),
and right after the last member it finds.
