# Streaming extraction supersedes duplicate names

## Why

`extract_all()` over a streaming TAR that holds one name twice wrote the first copy, then
failed the second with `Destination already exists` under the default overwrite policy.
Random access reports the first copy `SUPERSEDED` and writes only the second. A
streaming pass has no member list up front, so it cannot know the first copy is shadowed
until the second one arrives. The two modes should end in the same state on disk
whenever the archive order allows it.

## What changes

- `safe-extraction`: when a streaming pass meets a name it already handled, it reports the
  earlier copy `SUPERSEDED` and stops counting it against the entry cap, and against the
  byte cap unless a hardlink written in between still holds its bytes, before
  the later copy is filtered. The earlier copy's entry stays until the later copy is
  done, so a later copy at the same path replaces it atomically; otherwise it is then
  removed. Results and the tree on disk then match random access. The requirement lists
  the cases it cannot match, all of which depend on something that happened before the
  later copy arrived.

## Impact

Code: `src/archivey/internal/extraction.py`. Tests: `tests/test_extraction.py`.
