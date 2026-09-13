# Drop the unimplemented solid re-decode warning from the reading spec

## Why

`archive-reading/spec.md` states, in the `stream_members` requirement and again in
its scenario matrix, that random `open()` on a solid archive "may re-decode from block
start **and warn** to prefer `stream_members()`".

Nothing warns. There is no `DiagnosticCode` for it, no `logger.warning` on the
random-open path in the 7z, RAR or TAR backends, and no test asserting one — which is
why the gap survived. The code that sounds like it, `STREAM_REWIND_REDECOMPRESSES`,
has a single emission site (`internal/streams/archive_stream.py:442`) and fires on a
backward `seek()` **inside one member**, a different event.

Found while writing `docs/reading-members.md`: the page nearly repeated the spec's
claim to users.

## What changes

The spec stops promising the warning, and says instead how a caller does find out.
No behaviour changes — this aligns the spec with what has always shipped.

The maintainer's reason for not adding the diagnostic is recorded here because it is
a boundary worth keeping: **diagnostics describe the archive, not the caller's usage
pattern.** A malformed name, an unverifiable digest, a missing EOF marker are all
properties of the bytes. "You opened members out of order" is a property of the
program doing the reading, and belongs in the API documentation.

So the discovery path becomes:

- `reader.cost.access_cost == SOLID`, already exposed and already documented on
  `docs/access-and-cost.md` and `docs/reading-members.md`.
- The `ArchiveReader.open()` and `ArchiveReader.read()` docstrings, which now carry
  the cost note and render into the published API reference.

A plain `warnings.warn` remains possible and is deliberately **not** decided here.

## Impact

- `openspec/specs/archive-reading/spec.md` — one requirement, two clauses.
- `src/archivey/reader.py` — docstrings on `open()` and `read()`.
- No behavioural change, no test changes, not breaking.
