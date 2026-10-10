# Read TAR with archivey's own parser instead of stdlib `tarfile`

## Why

The TAR backend reads through stdlib `tarfile`, and a growing share of it exists to work
around `tarfile`. An inventory on 2026-10-10 found 20 places, about 650 of the backend's
1 810 lines at the time, 7 of them on private `tarfile` methods. The open TAR fix PRs add about 350
more lines, nearly all of them overrides of private parse methods, one of them a copy of
two stdlib function bodies pinned by source hashes.

The cost is not only lines:

- **Wrong answers that cannot be fixed on top of `tarfile`.** `tarfile` overwrites a
  sparse member's stored size with its logical size, so up to 511 bytes of padding can
  be served as data. It decides on directories before it knows the final name, so an
  old-style directory's data was parsed as members that are not in the archive.
- **Memory bounds that are hooks, not structure.** `tarfile` reads extended headers and
  sparse maps whole before anything can weigh them, keeps a second list of every
  header for the whole pass, and recurses on header chains.
- **Behaviour that depends on the Python patch level.** The same 2.5 KiB archive lists
  three members on Python 3.12.13 and two on 3.12.3 (Ubuntu) and 3.13.16, because
  upstream changed the private functions we hook. Upstream is still changing them.

TAR is the simplest format archivey reads. The case does not rest on line count: the
backend ends up about the same size (about 2 220 lines against 1 917 on `main` on
2026-10-10, and about 2 140 once PRs 704 and 716 merge; `design.md` §"Module layout"). What goes is the
workaround layer: the 20 sites, the 7 private-API hooks, the hash-pinned stdlib copies
and the ~350 lines the open PRs add. The maintainer moved it into 0.2.0 on 2026-10-10.

## What Changes

- New `internal/backends/tar_parser.py`: v7, ustar and old GNU header blocks, GNU
  base-256 numbers, PAX records (per-member and global), GNU long names and links, and
  the four GNU sparse encodings (old GNU, PAX 0.0, 0.1, 1.0), and the walker that
  reads them from one byte stream in a loop. Fuzzed.
- New `streams/streamtools/sparse.py`: a member's logical bytes over its stored bytes,
  holes as zeros, seekable when its source is.
- `tar_reader.py` reads headers with an iterative walker over the same byte stream it
  uses today (the source, or archivey's codec stream) and serves members as slices of
  it. Every `tarfile` hook, override and copied function goes.
- Fixes that fall out: a member `seek` past the end behaves as in every other format;
  a sparse member never serves its padding; `raw_name` is always the stored bytes; a
  GNU incremental archive (`tar -G`) lists its real names, not names under a directory
  of digits; a streaming pass keeps one member list, not two; the listing no longer
  depends on the Python patch release.
- `tarfile` stays as the test-fixture writer and a differential-test oracle.

No public name, signature, exception type or diagnostic code changes.

## Impact

- Capabilities: `format-tar`: a new requirement for what the parser reads; format
  properties, the handle lock, metadata mapping, hardlink lookup, truncation detection
  and name decoding rewritten without `tarfile`.
- Code: `internal/backends/tar_reader.py` (rewritten), `tar_parser.py` and
  `streamtools/sparse.py` (new), `streamtools/binaryio.py` (one `tarfile` workaround
  removed).
- Docs: handbook `formats/tar.md`, `known-issues.md` (two TAR entries removed),
  `threat-model.md` TAR notes, `docs/formats.md`.
- Sequencing: coding of the switch starts after PRs 704, 706 and 716 merge; their tests
  are acceptance tests.
