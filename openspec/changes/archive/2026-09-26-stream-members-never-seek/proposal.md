# A `stream_members()` handle never seeks

## Why

`archive-reading` said that `stream_members()` yields are a single-pass decode and that
`seekable_members=True` "does not require those handles to seek". It did not forbid it,
and the handles differed by format. Under `seekable_members=True` over a file source,
ZIP, TAR, compressed TAR, non-solid RAR, ISO, directory and single-file handles reported
`seekable()` true and seeked; solid 7z and solid RAR handles did not, because a solid
block or an `unrar` pipe cannot go back. That difference was an accident of each
backend, and a caller who seeks a ZIP pass handle has code that breaks on a 7z.

Maintainer decision (davitf, 2026-09-26): a `stream_members()` handle is never seekable,
and tests make sure the behaviour is the same on every format. Streams from an archive
opened without `seekable_members` are never seekable either (unchanged).

The reason is the one the spec already gave for a pass: it is a single-pass decode and
the iterator owns the position. A seek would decode again behind the iterator's back,
and on solid or piped members it cannot be done. Making the rule uniform means no caller
can come to rely on a per-format accident. A caller that needs to seek uses random
`open()` under `seekable_members=True`, which is what that flag declares.

## What changes

- `archive-reading`: a `stream_members()` handle SHALL report `seekable() is False` and
  `seek()` SHALL raise `io.UnsupportedOperation`, whatever `seekable_members` and
  `streaming` are, on every format; `tell()` SHALL work. The rule that nothing seeks
  without `seekable_members` stays.
- `testing-contract`: one parametrized test covers the five cases over one format matrix.

This is a behaviour change for a caller that seeked a `stream_members()` handle on ZIP,
TAR, ISO, directory, single-file or non-solid RAR archives, and for a caller that
opened a nested archive from one: `open_archive()` now sees a non-seekable source.

## Impact

Code: `ArchiveStream._make_forward_only()`, called on every yielded handle in
`BaseArchiveReader._iter_stream_members` (one place for every backend). RAR
`_UnrarOwnedStream.tell()` counts bytes instead of asking the pipe, which raised
`OSError`. Tests: `tests/test_member_stream_contract.py`. Docs:
`docs/access-and-cost.md`, `docs/reading-members.md`, `CHANGELOG.md`.
