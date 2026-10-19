## Why

A ZIP read from a pipe learns each member's type, mode, host and current copy only from
the central directory, after the last member. Today `extract_all` writes every member into
the destination as it arrives, then revises what it wrote once the directory is read. The
revisions include:

- turning files into links
- renaming members
- taking back members the selection no longer takes
- moving the caller's entries aside and putting them back
- removing, on an early stop, every file that might be a link

That is roughly 600 lines of `internal/extraction.py`, for one reader (the forward ZIP
walk). The edge cases it carries keep producing review findings.

Staging the member data in a private directory inside the destination, and placing
members only once the directory is read, would replace those revisions with the
ordinary extraction path. It would also remove several of the places where a pipe
extraction differs from a file extraction.

## What Changes

- From a pipe, the pass writes each selected member's data under an index name in one
  `.archivey-tmp-*` directory directly under the destination root, and writes nothing
  else into the destination.
- Once the central directory is read, the run places the members with the same per-member
  path a file extraction uses. Each member is read from its staged file, and a staged file
  lands by rename, so no data is copied twice. The filter, the policy checks, the
  overwrite policy, collisions and `abort_on` each run once, on the final member.
- **BREAKING (pipe ZIP only):** a run that stops before the central directory is read
  leaves nothing of its own in the destination. Today the files longer than 4096 bytes
  stay, with no mode applied.
- **BREAKING (pipe ZIP only):** the filter runs once per member, at placement. Today it
  runs during the pass and again at the end.
- A staged directory that cannot be removed is left as `.archivey-tmp-*`, which the docs
  already call safe to delete.
- The caller's entries are never moved aside, and no file that might be a link is ever
  written into the destination.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `safe-extraction`: the requirement "A ZIP extracted from a non-seekable source is
  settled at the end of the pass" describes staging and placement in place of revision.
  Its list of the places where the two reads MAY differ (DR-5a) gets shorter.
- `archive-reading`: in "Collection form of MemberSelector", the filter is called once,
  at placement. A filter that selects by a name only the directory gives (a DOS host's
  `docs\r.txt`) then extracts the member from a pipe as from a file; today the member
  fails.
- `format-zip`: in "Read a non-seekable ZIP source in one forward pass", the row for a
  run that stops before the end of the pass changes, and the row "No implicit
  seekable-copy, temp-file, or spool fallback" states that staged member output is not
  a copy of the source.

## Impact

- `src/archivey/internal/extraction.py`: the revising-pass machinery is removed. A
  staging writer and a placement loop take its place, and the loop reuses the per-member
  path.
- `src/archivey/internal/backends/zip_forward_reader.py`: unchanged.
  `_REVISES_YIELDED_MEMBERS` still marks the reader whose pass extraction stages.
- `docs/extracting.md`: the leftover note covers a `.archivey-tmp-*` directory, and the
  pipe-ZIP section describes the stop behaviour.
- Tests: the pipe-ZIP extraction tests that pin today's behaviour on a stopped run, on
  the caller's entries set aside, and on the filter being called twice change with the
  spec. The rest must pass unchanged.
- No public API, configuration or dependency change.
