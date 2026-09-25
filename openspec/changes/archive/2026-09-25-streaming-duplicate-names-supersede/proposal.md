# Streaming extraction supersedes duplicate names

## Why

`extract_all()` over a streaming TAR that holds one name twice wrote the first copy, then
failed the second with `Destination already exists` under the default overwrite policy.
Random access reports the first copy `SUPERSEDED` and writes only the second. A
streaming pass has no member list up front, so it cannot know the first copy is shadowed
until the second one arrives. The two modes should end in the same state on disk
whenever the archive order allows it.

## What changes

- `safe-extraction`: when a streaming pass meets a name it already handled, it removes
  what it wrote for the earlier copy and reports that copy `SUPERSEDED`, before the
  later copy is filtered or written. Results and the tree on disk then match random
  access. The one case it cannot match is a different name colliding with the earlier
  copy in between (a case variant outside `TRUSTED`), because that collision is decided
  before the later copy is seen.

## Impact

Code: `src/archivey/internal/extraction.py`. Tests: `tests/test_extraction.py`.
