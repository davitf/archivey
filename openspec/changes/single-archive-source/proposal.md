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
  caller stream, or a volume list, and the only source object detection and every
  backend receive. It **is** a read-only binary stream (third-party parsers — `zipfile`,
  `tarfile`, `pycdlib` — are handed it directly) and carries every source-level
  guarantee itself:
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
- No public API change. `open_archive`, `open_stream` and `detect_format` accept the same
  sources and behave the same way.

## Capabilities

### New Capabilities

### Modified Capabilities
- `access-mode-and-cost`: the source-boundary requirement states the guarantees as
  properties of the one object the boundary returns, rather than as a table of which
  wrapper each source kind gets, and gains the bounded-read guarantee at the raw source.

## Impact

- New `src/archivey/internal/source.py`; `volumes.py` (`resolve_source`,
  `ResolvedSource`, volume items), `core.py` (dispatch, `open_stream`), `detection.py`,
  `streams/peekable.py`, `streams/streamtools/full_count.py` (removed),
  `streams/streamtools/shared.py` (`SharedSource` takes the source's ownership answer
  instead of deciding its own), and every backend that opens its source: ZIP, TAR, ISO,
  RAR, 7z, single-file, directory.
- Depends on the borrowed-source-streams work reaching `main` first; the
  bounded-header-allocations work it also consolidates is already there.
- Tests: the full-count, ownership and short-read suites retarget `ArchiveSource`;
  `tests/test_stream_bases.py`'s close inventory loses three classes; the leak oracle
  and the caller-stream parity tests must pass unchanged, which is the refactor's proof.
- Docs: `dev-docs/topics/stream-ownership.md`, the source rows of
  `dev-docs/threat-model.md`.
- Performance: one Python-level `read` call is added in front of the seekable-raw case,
  which today reaches the C buffer directly; measured with
  `benchmarks/caller_stream_probe.py` before and after, and reported on the PR.
