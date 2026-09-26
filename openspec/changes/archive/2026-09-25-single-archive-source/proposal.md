## Why

A caller's source reaches a backend through a stack of single-purpose wrappers, each
added for one guarantee: `BorrowedStream` so archivey never closes the caller's object,
`FullCountStream` or a non-closing `io.BufferedReader` so a short `read(n)` cannot break
a header parser, and `_ImageBoundedStream` in the ISO backend so a size field in the
image cannot drive a 4 GiB allocation. Each is correct on its own, but which of them is
present depends on the source's type, the backend, and whether detection ran, and every
backend still branches on `isinstance(source, Path)` and keeps its own `_owned_fp` /
`_owned_stream` bookkeeping for the handle it opened. The guarantees are real; where
they live is hard to see and easy to break.

## What Changes

- One internal class, `ArchiveSource`, is what `resolve_source` builds from a path, a
  caller stream, or a volume list, and the source object detection and every backend
  receive. It **is** a read-only binary stream (third-party parsers such as `tarfile` and
  `pycdlib` are handed it directly) and carries every source-level guarantee itself:
  - **full-count** `read(n)`, choosing internally between passing through an
    already-buffered source, a fixed-size read buffer over a seekable raw one, and
    gathering over a non-seekable one;
  - **ownership**: it closes what archivey opened or built (a path's handle, a
    joined volume set, its own buffer) and never the caller's object;
  - **bounded reads**: no `read(n)` asks the source for more than it can still supply,
    clamped by a length that is a fact and stepped when the length is unknown, so a
    size field an archive declares cannot become an allocation at the source;
  - **cheap facts**: the path when there is a real file (`unrar`, volume discovery,
    path-only codec accelerators), the volume paths, the size and whether that size is
    a fact, measured once at construction.
- The replay prefix detection reads from a non-seekable source lives in `ArchiveSource`
  too, so `open_archive` and `open_stream` no longer swap the source for a
  `PeekableStream` before detection.
- A path source opens its handle lazily, so a backend that only needs the path (the
  directory reader, `unrar`) opens nothing.
- `BorrowedStream`, `FullCountStream`, the non-closing buffered reader,
  `ensure_full_count_reads` and the ISO backend's `_ImageBoundedStream` are removed;
  their tests become `ArchiveSource`'s.
- Backends stop branching on `Path` versus stream for the source and stop tracking the
  source handle's ownership; the reader closes its `ArchiveSource`.
- Out of scope, and kept as separate layers: member-level streams (slices, shared views,
  decompressors, verifiers), measurement (`SeekCountingStream` stays an optional wrapper
  put on from outside), and the bound on reads of *decoded* bytes (the TAR backend's
  wrapper over a decompressor keeps its own).
- Three wrappers stay above `ArchiveSource`, each preserving its guarantees: measurement,
  ZIP's start offset, and the zero-origin `SlicingStream` that `fix_stream_start_position`
  puts over a mid-positioned seekable stream. ZIP and the single-file codecs keep handing
  their parser the path when they need no handle of archivey's.
- No public API change. `open_archive`, `open_stream` and `detect_format` accept the same
  sources and behave the same way.

## Capabilities

### New Capabilities

### Modified Capabilities
- `access-mode-and-cost`: the source-boundary requirement states the guarantees as
  properties of the one object the boundary returns, rather than as a table of which
  wrapper each source kind gets, and gains the bounded-read guarantee at the raw source.
- `format-detection`: the non-seekable replay prefix is the `ArchiveSource`'s, not a
  `PeekableStream` the opener adds.
- `testing-contract`: the short-read coverage requirement asserts the boundary on the
  `ArchiveSource` rather than on `ensure_full_count_reads`, and no longer says that
  function returns a caller's buffer unchanged, which stopped being true when caller
  streams became borrowed.
- `backend-registry`: detection's probes go through the detection workspace, not a named
  `PeekableStream`.

## Impact

- New `src/archivey/internal/source.py`; `volumes.py` (`resolve_source`,
  `ResolvedSource`, volume items), `core.py` (dispatch, `open_stream`), `detection.py`,
  `detection_workspace.py`, `streams/peekable.py` (removed, or reduced to whatever
  non-source call site survives the migration), `streams/streamtools/full_count.py`
  (removed),
  `streams/streamtools/shared.py` (`SharedSource` takes the source's ownership answer
  instead of deciding its own), and every backend that opens its source: ZIP, TAR, ISO,
  RAR, 7z, single-file, directory.
- Builds on the borrowed-source-streams and bounded-header-allocations work, both on
  `main`: it consolidates what those two add.
- Tests: the full-count, ownership and short-read suites retarget `ArchiveSource`;
  `tests/test_stream_bases.py`'s close inventory loses three classes; the leak oracle
  and the caller-stream parity tests must pass unchanged, which is the refactor's proof.
- Docs: `dev-docs/topics/stream-ownership.md`, the source rows of
  `dev-docs/threat-model.md`.
- Performance: one Python-level `read` call is added in front of the seekable-raw case,
  which today reaches the C buffer directly, and in front of streaming TAR over a path;
  measured with `benchmarks/caller_stream_probe.py` before and after, with path sources
  as a treatment rather than the control, and reported on the PR.
