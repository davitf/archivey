# 0021 — Native TAR reader; stdlib `tarfile` leaves the library

- **Status:** accepted
- **Date:** 2026-10 (implementation via the `native-tar-reader` OpenSpec change)
- **Provenance:** OpenSpec `native-tar-reader`, proposed 2026-10-10 and archived
  2026-10-12 as `openspec/changes/archive/2026-10-10-native-tar-reader/` (`proposal.md`,
  `design.md`); `dev-docs/formats/tar.md` §6; PRs #736 (design), #738 (parser), #739
  (sparse stream), #754 (switch), #757 (specs and docs)

## Context

The TAR backend read headers through stdlib `tarfile` because it cost no dependency. By
October 2026 a large share of the backend existed to work around it. An inventory on
2026-10-10 counted 20 places, about 650 of the backend's 1 810 lines, and 7 of them used
private or undeclared `tarfile` API. The TAR fix PRs open at that time added about 350
more lines. Nearly all of them overrode private parse methods, and one copied two stdlib
function bodies and pinned them with source hashes.

The cost was not only lines. Three of archivey's hard rules could not be met on top of
`tarfile`:

- **Never wrong bytes (DR-1).** `tarfile` overwrites a sparse member's stored size with its
  logical size, so up to 511 bytes of padding could be served as data. It decides that a
  header is a directory before it knows the final name, so an old-style directory's data
  was parsed as members that are not in the archive. A wrapper can only refuse such
  members after `tarfile` has already lost the fact it needed.
- **Bounded memory on hostile input (DR-9).** `tarfile` reads extended headers and sparse maps
  whole before anything can weigh them. It keeps a second list of every header for the
  whole pass, and it recurses on header chains. Each bound was a hook in front of a
  private read. Two growth sources could not be hooked at all: the duplicate
  `TarFile.members` list, and the recursion on header chains, which archivey could only
  catch as a `RecursionError` after the fact.
- **The same answer on every Python version (DR-5).** One 2.5 KiB archive listed three
  members on Python 3.12.13 and two on Ubuntu's 3.12.3 and on 3.13.16, because upstream
  changed the private functions archivey hooked and distributions backport those
  changes. The version number alone did not say which result a caller would get, and
  upstream was still changing those functions.

`tarfile` also did not say why a walk stopped. archivey inferred it from exception
message text and traceback frames, and in a streaming pass a corrupt last header could
end the listing without an error.

## Decision

Parse TAR structure natively, in pure Python, as 7z and RAR already are (ADR 0001, 0002).
`internal/backends/tar_parser.py` reads v7, ustar and old GNU headers, GNU base-256
numbers, PAX records, GNU long names and the four GNU sparse encodings. Its walker reads
one header at a time from one byte stream and reports why it stopped.
`internal/streams/streamtools/sparse.py` serves a sparse member's logical bytes. Member data still goes
through the shared codec layer. `tarfile` stays a test-fixture writer and a
differential-test oracle, and `src/` never imports it.

## Consequences

- Still zero-dependency (ADR 0011). The parser is archivey's to maintain. TAR is a frozen
  format. A change to the parser is checked against `tarfile` as the differential oracle
  (`tests/test_tar_parser_differential.py`), against archives GNU tar writes for the
  shapes `tarfile` cannot write (those cases skip where GNU tar is not installed), and by
  the fuzz target `tests/fuzz_tar_parser.py`.
- The backend got bigger. Just before the switch (#754) `tar_reader.py` was 2 249 lines.
  At the switch `tar_reader.py`, `tar_parser.py` and `sparse.py` were 1 371, 1 253 and 171
  lines, 2 795 in all: about 550 lines (24 %) more. The proposal had estimated about
  2 220. What went is the workaround layer: the private-API hooks, the copied stdlib
  functions and their source hash tests, and the message-text matching.
- The listing no longer depends on the Python patch release.
- A rejected header, or a cut inside a header or member data, raises `CorruptionError` or
  `TruncatedError` where the walk stopped, in both access modes, instead of ending the
  listing early. A missing or short trailer still warns with
  `ARCHIVE_EOF_MARKER_MISSING` and the listing completes.
- Extended headers and sparse maps are charged to `max_metadata_bytes` before they are
  read, and a pass keeps one member list, not two.
- A sparse member never serves its padding. `raw_name` is always the stored bytes. A
  member `seek` past the end behaves as in every other format. GNU dumpdir entries (`tar
  -G`) list as directories. PR #754's description lists every behaviour that changed.
- Performance, measured on PR #754 (CPython 3.11, best of 5): listing and random access
  are level with the `tarfile`-based reader, and a `stream_members()` pass over many
  small members is 18 % to 24 % faster. Reading 4 × 128 MiB members is 31 % faster,
  level with `tarfile` itself. Both archivey readers take about twice as long as
  `tarfile` to list. That cost comes from what archivey adds per member, not from the
  parser.
- The walker validates each header at its offset, so a later salvage mode could resync
  past a bad header, as GNU tar does. This is open (`formats/tar.md` §7).
