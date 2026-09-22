## Context

`resolve_source` (`internal/volumes.py`) is the one boundary every archive source
crosses. It returns `ResolvedSource(open_source: Path | BinaryIO, archive_name,
volume_count)`, and what `open_source` is depends on what the caller passed:

| Caller passed | `open_source` today | Built by |
| --- | --- | --- |
| a path to a file | the `Path` | — (the backend opens it) |
| a path to a directory | the `Path` | — |
| a path with numbered siblings | `ConcatenatedFile` of paths | `join_volumes` |
| a RAR first volume | the `Path` of volume 1 | — (`unrar` walks the set) |
| an already-buffered stream (`BytesIO`, `open()`) | `BorrowedStream` | `ensure_full_count_reads` |
| a seekable raw stream | a non-closing `io.BufferedReader` | `ensure_full_count_reads` |
| a non-seekable raw stream | `FullCountStream` | `ensure_full_count_reads` |
| a list of streams / mixed | `ConcatenatedFile` of the above | `_coerce_path_or_stream` |

After that, `core.open_archive` may replace a non-seekable stream with a
`PeekableStream` so detection's prefix is replayed, and each backend adds its own layer:
the ISO backend puts `_ImageBoundedStream` in front
of whatever it got so pycdlib's header-sized reads cannot allocate past the image; the
TAR backend's `_EofProbeStream` bounds the same way for `tarfile`; measurement adds
`SeekCountingStream`.

Source-level decisions are made again in every backend, keyed on `isinstance(source,
Path)`:

| Where | What the branch decides |
| --- | --- |
| `core.py` (3) | directory dispatch; following an SFX stub to its volume |
| `zip_reader.py` | opening its own handle under measurement or a start offset (`_owned_fp`) |
| `tar_reader.py` (2) | opening its own handle (`_owned_stream`); the stream-capability answer |
| `iso_reader.py` | opening its own handle (`_owned_fp`) |
| `rar_reader.py` (2) | whether `unrar` needs a disk copy; sibling discovery (`_owned_concat`) |
| `single_file_reader.py` | handing the path to the codec for independent handles and path-only accelerators |
| `directory_reader.py` | refusing anything but a path |
| `SharedSource` (7z, RAR, single-file) | open-and-own for a path, borrow for a stream |

Thirty-three lines in `src/` name `_owned_fp`, `_owned_stream` or
`_owned_concat`. Each backend's close path has to get the same rule right: close what it
opened, never what the caller passed.

## Goals / Non-Goals

**Goals:**
- One object carries every guarantee the raw source must give. Every consumer of the raw
  source — detection, each backend, the third-party parsers a backend hands it to —
  receives that object, a wrapper over it that keeps the guarantees (decision 7), or its
  path for a parser that opens the file itself (decision 4).
- One ownership rule, stated once: archivey closes what it opened or built.
- The bound on reads sized from an archive's own fields holds at the raw source for
  every backend, not only the ones where an allocation was found.
- No public API or behaviour change. On a path source, no Python frame is added where a
  parser reads the file itself today (decision 4); where the design adds or replaces one,
  the probe measures it with `path` as a treatment shape, not as the control.

**Non-Goals:**
- Member-level streams (`SlicingStream`, `SharedView`, decompressors, verifiers). They
  sit above the source and have their own ownership, already recorded in
  `dev-docs/topics/stream-ownership.md`.
- Measurement. `SeekCountingStream` stays an optional wrapper the reader puts on from
  outside; folding it in would make every read pay for a counter nobody asked for.
- The bound on reads of **decoded** bytes. `tarfile` over a decompressor is sized by
  the archive too, but the source is not where that read lands; the TAR backend's wrapper
  keeps that bound.
- Bounded source spooling (a separate change), which decides when a non-seekable source
  may be copied. `ArchiveSource` is where that copy would live; this change does not
  add it.

## Investigations

**Cost of one Python-level layer at the source.** Measured when `BorrowedStream` was put
in front of already-buffered sources, with `benchmarks/caller_stream_probe.py`
(alternating runs, per-case minimum, path sources as the control): about 0.04 µs per
`read` call, a median of about 2.5 % on stream sources across ZIP, TAR, and solid and
many-member 7z, and nothing on path sources, which that change did not touch. That is the
price of the pass-through case here as well. Two cases are new and unmeasured. The
seekable-raw case today reaches the C `BufferedReader.read` with no Python frame, and
`ArchiveSource` adds one. On a path source the picture differs by backend: TAR in random
access and ISO already read through a Python wrapper (the EOF probe, the image bound), which
`ArchiveSource` replaces rather than adds to; streaming TAR hands `tarfile` the raw handle
today, and gains a frame per `tarfile` block read; ZIP and the single-file codecs open the
path themselves and keep doing so (decision 4). The probe measures both, with `path` as a
treatment shape and the odd/even split of each side's own runs as the noise control.

**Bounding is free below the step.** `read_within_reach` issues a single `read` when the
request fits in one step (16 MiB) or the length is a fact, so bounding every read at the
source costs a subtraction on the normal path and a join copy only for a single request
over 16 MiB against a source of unknown length.

## Decisions

### 1. `ArchiveSource` is itself the stream

It subclasses the read-only stream base and is handed directly to `tarfile`, `pycdlib`,
the 7z parser, and `zipfile` whenever ZIP needs a handle of archivey's (decision 4). A
holder object with a `.stream` attribute would leave every backend choosing which of
`.path` / `.stream` / something wrapped to pass on for every read; here the stream is the
default and `.path` is the named exception.

**Rejected:** a `SourceSpec` value object passed alongside the stream. Two things to keep
in step, and the stream half would still need every guarantee.

### 2. The full-count strategy is internal and chosen once

At construction it inspects the inner once and picks: pass-through for an
`io.BufferedIOBase`; a fixed-size `io.BufferedReader` it builds and owns over a seekable
raw inner, detached rather than closed so the caller's raw survives; gathering for a
non-seekable raw inner. The three are private strategies behind one `read`, not three
classes a reader of the code has to tell apart.

**Rejected:** keeping the three classes and adding a fourth that composes them. That is
the onion, one layer higher.

### 3. Ownership: close what archivey opened or built, never the caller's object

A path source's handle, a `ConcatenatedFile` built from a volume list, and the buffer from
decision 2 are archivey's and close with the `ArchiveSource`. A caller's object, including
each caller stream inside a volume list, is borrowed. Backends stop opening the source
themselves, so `_owned_fp` / `_owned_stream` / `_owned_concat` for the source go away and
the reader's close path closes one object. `SharedSource` takes the `ArchiveSource` and
does not decide ownership of its own.

### 4. A path source opens lazily and keeps its path

`.path` is set whenever a real file exists (and `.volume_paths` for a joined set of
paths), so `unrar`, volume discovery, SFX-stub following and path-only codec features
keep working. The handle opens on first stream use, so a backend that only needs the path
— the directory reader, `unrar` over a path — opens no file descriptor it will not read.
A directory is an `ArchiveSource` with a path and no stream; reading it raises.

A third-party parser that opens a path itself keeps receiving `.path`, and the
`ArchiveSource` then opens nothing. Two backends do: ZIP, whose `zipfile` bounds its
central-directory read by the end record's own position (it refuses a directory that would
start before offset 0, so the declared size cannot exceed the file), and the single-file
codecs, which want independent handles and path-only accelerators. Each switches to the
`ArchiveSource` when it needs a handle archivey controls: measurement, or ZIP's start
offset. This keeps a plain ZIP open from a path at zero archivey frames per read, as today.

Detection over a path opens and closes its own handle from `.path`, as the detection
workspace does today. So the `ArchiveSource`'s handle opens only when a backend reads from
it, and a backend that never does (`unrar` over a path, a directory) leaves no handle open
on the archive — which matters on Windows, where an open handle blocks deleting or
renaming the file. Standalone `detect_format` builds no `ArchiveSource`: it takes a path or
stream directly and keeps its own non-consuming handling.

### 5. Bounded reads are the default `read`, not a separate method

pycdlib and `tarfile` call `read(n)` with a size taken from the archive; they will not
call an archivey method. So `read` itself never asks the inner for more than the source
can still supply: clamped when the remaining length is a fact, stepped when it is not
(`read_within_reach`, the rule the ISO and TAR bounds already use).

**Order: the bound decides how many bytes may be requested, and the full-count strategy
then serves that number.** `read_within_reach` returns whatever one inner `read` gives when
it clamps, and returns early on a short read below the step, both correct over a full-count
stream and both short over a raw one. So it runs over the chosen full-count strategy, never
over the raw inner: the gatherer re-asks within the bound, and a step is one full-count
request. The size is measured
once at construction together with whether it is a **fact** — `stat` on a path, a
`BytesIO`'s buffer, `fstat` on a regular file — as distinct from a duck-typed `size`
attribute on a caller's object, which is a hint. Only a fact clamps; a hint still steps.
Clamping on a hint that understates would truncate a legitimate read, which is the
failure `read_within_reach`'s documentation warns about.

With this, the ISO backend's `_ImageBoundedStream` has nothing left to do and is
removed. The TAR backend's wrapper stays, bounding its decoded input (Non-Goals), and
stops bounding the raw case twice.

**Rejected:** a `read_bounded(n)` method alongside an unbounded `read`. Every third-party
parser would get the unbounded one, which is where the allocations were found.

### 6. The size and the name are answered by the source, not probed through it

`ArchiveSource` exposes `size` only when it is a fact and answers `name` itself, so
`source_byte_size` and `source_name` stop needing `peel_for_source_size` to see through a
wrapper at the source. The peel flag stays for the member-level wrappers that still use
it.

### 7. Three wrappers stay above it

`ArchiveSource` does not absorb every layer above the source; three stay, each a wrapper
over it that preserves its guarantees (the rule the spec delta states):

- **Measurement.** `_track_source_seeks` wraps the `ArchiveSource` when measurement is on,
  as it wraps the source today, so the counter sees exactly the seeks the backend issues.
  Folding it in would make every read pay for a counter nobody asked for.
- **ZIP's start offset**, a `SlicingStream` that makes an SFX payload start at 0.
- **The zero-origin view.** `open_archive` passes a mid-positioned seekable stream through
  `fix_stream_start_position` after detection, which returns a `SlicingStream` so a backend
  sees position 0 at the first archive byte.

The last two are the same view, and `SlicingStream` clamps its reads to the slice and does
not close its inner by default, so both preserve the guarantees. They stay a layer rather
than moving in, as the replay prefix does (decision 8), because the replay prefix is state
only the source can hold, while an origin shift is position arithmetic `SlicingStream`
already does and tests; moving it in would duplicate that arithmetic, not remove a layer
anyone has to reason about.

### 7a. Volume lists

A volume list of caller streams nests one borrowed `ArchiveSource` per part inside the
joined `ConcatenatedFile`, and one `ArchiveSource` over the whole. The parts carry ownership
(each borrows its caller stream) and full-count, but do not bound; the outer one bounds
once. A joined set's size is a fact when every part's is: the sum of `stat` sizes for path
volumes, and for stream volumes the lengths `ConcatenatedFile` already measures to place its
offsets, which are as much a fact as those offsets.

### 8. The detection replay prefix moves in

**Maintainer decision (davitf, 2026-09-22):** fold it in. For a non-seekable source,
`open_archive` and `open_stream` today replace the source with a `PeekableStream` so
detection's bytes are replayed to the backend, which is the one place a backend still
receives something other than the `ArchiveSource`. The `ArchiveSource` over a
non-seekable source holds the replay prefix itself: `peek(n)` fills it without
consuming, and `read` drains it before reaching the source. The rebinding in `core.py`
goes, and the detection workspace peeks the `ArchiveSource` it is handed.

The prefix is bounded by the detection limit, as `PeekableStream`'s is, so this adds no
buffering the source does not already pay for. `PeekableStream` is removed, or kept only
where a call site the migration finds still needs a replay buffer over something that is
not a source.

**Rejected:** keeping `PeekableStream` as a separate layer for non-seekable sources. It
would leave the backend's source object depending on whether detection ran, which is the
branching this change removes.

## Risks / Trade-offs

- [A Python frame is added in front of the seekable-raw case] → Measure with the probe
  before and after; if it shows, the pass-through strategy can hand back the C buffer's
  bound method for `read`/`readinto` at construction rather than dispatching per call.
- [Moving every backend at once is a large diff that touches ownership, where a mistake is
  a closed caller handle or a leak] → Migrate one backend per task, each behind the leak
  oracle and the caller-stream ownership suite, which run on every test and fail on the
  first stray close or leak.
- [Two live changes modify the same `access-mode-and-cost` requirement: this one and
  bounded source spooling] → Whichever archives second re-derives its MODIFIED block from
  the spec as it then stands, rather than from the copy it was written against.
- [`read` bounding a hint-sized source by stepping costs a join copy for a single request
  over 16 MiB] → Only on sources whose length is not a fact; the same cost the ISO and TAR
  backends accept today, now paid in one place.
