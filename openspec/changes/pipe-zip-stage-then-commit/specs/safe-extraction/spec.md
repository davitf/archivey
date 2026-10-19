## MODIFIED Requirements

### Requirement: A ZIP extracted from a non-seekable source is settled at the end of the pass

A ZIP read from a non-seekable source (`streaming=True`) gives each member's type, mode,
host and comment, and the order that decides `is_current`, only in its central
directory, after the last member. During the pass, `extract_all` SHALL write the data of
each member the selection may take into one staging directory, named `.archivey-tmp-`
and a random suffix, directly under the destination root, and SHALL write nothing else
into the destination. Once the directory has been read, it SHALL place the members in
listing order, as the extraction of the same archive from a file places them, each read
from its staged data:

- The selection, the caller's `filter`, the policy's checks, the overwrite policy, name
  collisions and `abort_on` SHALL each act once per member, on the member as the
  directory describes it, when the member is placed. A symlink that escapes the
  destination is therefore refused from a pipe as from a file, and nothing is written
  through a link before every member is known.
- Five things are known only once the directory has been read, and each SHALL fail
  the member it names, under "Error Policy (OnError)" above: a size or CRC from a
  data descriptor that differs from the central entry; a local header whose CRC, sizes
  or encryption flag the central entry contradicts; two same-name members whose
  directory order reverses their file order (the current one fails, the other becomes
  `SUPERSEDED`); a directory entry with no local entry; and a local header and
  directory entry that store different names. The member's result SHALL be the one
  the extraction from a file gives it, or `FAILED` with `CorruptionError` where that
  result needs bytes the pass did not stage; the overwrite policy decides first. Under
  `OnError.STOP` the first such failure SHALL raise `CorruptionError` when placement
  reaches it.
- A local entry no directory entry points to SHALL NOT be placed, and SHALL be reported
  as `ARCHIVE_UNLISTED_DATA`.
- A run that stops before placement begins SHALL leave nothing it wrote in the
  destination. One that stops during placement SHALL keep the members already placed,
  as an extraction from a file keeps the members before its stop. The staging directory
  SHALL be removed when the run ends, whatever the outcome; one the filesystem refuses
  to remove stays, with a warning.
- The bomb limits SHALL count staged data as decoded output, and each staged member
  toward `max_entries`, when it is staged. A staged member that is not placed SHALL stop
  counting toward `max_extracted_bytes` and `max_entries`, and SHALL still count toward
  the archive-wide ratio. Placing a staged member adds nothing to any count.

Where the two reads MAY differ (DR-5a, shapes only crafted archives have): the
descriptor mismatch and the reversed pair fail a member the extraction from a file
writes; and the bomb limits count what the pass stages, so a member staged and then not
placed can still stop a run under `max_entries` or `max_extracted_bytes`.

#### Scenario: ZIP from a pipe, settled at the end of the pass

| Case | Expected |
| --- | --- |
| The directory types a member staged as a file as a symlink to `../../etc/passwd` | Refused when placed, as from a file; nothing of it reaches the destination |
| Two same-name members `M1`, `M2` in file order, the directory makes `M1` current, `OnError.CONTINUE` | `M1` `FAILED` with `CorruptionError`, `M2` `SUPERSEDED`; the path is empty |
| A STORED bit-3 member whose data embeds a descriptor for a prefix of itself | `FAILED` with `CorruptionError`; nothing placed for it; under `OnError.STOP` raised when placement reaches it |
| A directory entry with no local entry, selected | A `FAILED` result is added for it |
| A local entry the directory does not list | Not placed; `ARCHIVE_UNLISTED_DATA` |
| The run stops before the directory is read (`abort_on`, `on_error="stop"`, a limit) | The destination holds nothing the run wrote; the caller's entries are as they were |
| `overwrite="replace"` over a caller's file, for a member the selection drops once the directory is read | The caller's file is untouched |
| A `filter=` callable | Called once per member, with `member_state_final` true |
| The staging directory cannot be removed at the end of the run | It stays, named `.archivey-tmp-…`, with a warning |
