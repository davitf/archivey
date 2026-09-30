## ADDED Requirements

### Requirement: Count the RAR dictionary against the decoder memory cap

The reader SHALL refuse a member read with `ResourceLimitError` when the dictionary
memory the decompressing program would use is over
`DecoderLimits.max_decoder_memory`. The refusal SHALL come before `unrar` or `unar` is
spawned and before a stream source is copied to disk. The count SHALL follow the
program that will run, so under `rar_decompressor="auto"` it is the rule of the program
`auto` picked:

- `unar`: the largest dictionary declared in the member's solid stream, up to and
  including the member.
- `unrar`, nonsolid: for the member and for every earlier member its include mask also
  selects, the smaller of that member's declared dictionary and its unpacked size; the
  largest of these.
- `unrar`, solid: the smaller of the largest dictionary declared up to and including the
  member and the unpacked size of those members.

A stored member, a directory and a RAR5 redirect SHALL count 0. A `stream_members()`
pass that runs one process over the archive SHALL count, for each member, the largest
count of any member up to and including it, and SHALL check it on that member's first
read, so a pass that lists or skips a member is not refused for it. When the count is smaller than the
declared dictionary, the message SHALL give both numbers.

#### Scenario: dictionary cap matrix

| Case | Expected |
| --- | --- |
| `unar`, a small member declaring 4 GiB, default cap | `ResourceLimitError`; `unar` not spawned |
| `unrar`, a small nonsolid member declaring 4 GiB, default cap | Reads |
| `unrar`, a nonsolid member declaring 4 GiB and 3 GiB unpacked, default cap | `ResourceLimitError` naming 3 GiB and the declared 4 GiB; `unrar` not spawned |
| `unrar`, a solid member behind a member declaring 4 GiB and 3 GiB unpacked | `ResourceLimitError`, for a named read and in a `stream_members()` pass |
| `unrar`, a duplicate name whose earlier entry declares 4 GiB and 3 GiB unpacked | `ResourceLimitError` when the later, small entry is read |
| `rar_decompressor="auto"` | The count of the program `auto` picked |
