# Stream-wrapper ownership

Who calls `close()` on the stream underneath a wrapper. Not file-mode bits, not
archive member Unix uid/gid.

## 1. The rule

**A wrapper borrows unless its type says otherwise.** Silence on a view or a
decoder means "do not close the inner." Silence on a `DelegatingStream` means
"do close the inner." The keyword for the exception is `owns_inner`.

| Type | Default | Opt-in |
| --- | --- | --- |
| `SlicingStream` | borrow | `owns_inner=True` (4 production sites) |
| `SharedView` | borrow, hardcoded | none — Parcel B split this class so `lock=` could not switch modes |
| `DecompressorStream` | borrow | `owns_inner=True` on later pybcj BCJ stages (first-stage Copy+BCJ / BCJ-alone borrows the pack view) |
| `DelegatingStream` | **own** | `subclass_closes_inner=True` is *who* closes, not *whether* |
| `SharedSource` | Path → own; `BinaryIO` → borrow | encoded in the constructor argument type |
| ZIP/ISO `_owned_fp`, TAR `_owned_stream` | Path the reader opened | not a wrapper flag; leave the names |

The public contract is the borrow default: archivey never closes a
caller-supplied `BinaryIO` (`openspec/specs/archive-reading/spec.md`). Views and
decoders sit on those handles, so they borrow. `DelegatingStream` is a 1:1
stand-in in a close chain, so it owns.

## 2. Why `DelegatingStream` still owns

Flipping it to borrow was the tempting unification: then swapping
`DelegatingStream` for `SlicingStream` could not leak. The leak oracle already
catches that swap (`tests/leak_oracle.py` pins `owns_inner=True` slices and
live children). The remaining risk is the other direction.

Nine production subclasses. Seven ride the owning default with no keyword.
Counting wrappers (`CountingReader`, `OutputCountingStream`,
`SeekCountingStream`) are spliced mid-chain: their inner is always an
already-normalised non-closing wrapper (`_NonClosingBufferedReader` /
`SharedView` / `PeekableStream` / `ArchiveStream`), because `open_archive`
wraps a caller `BinaryIO` before any backend sees it. The owning default
never reaches a caller-supplied object.

| Class | Close must reach |
| --- | --- |
| `LockedStream` | tar `extractfile` / the `_PyCdlibStream` underneath |
| `CloseLockedStream` | `ZipExtFile` |
| `_PyCdlibStream` | `PyCdlibIO.__exit__` via `inner.close()` |
| `_GzipTruncationCheckStream` | the rapidgzip accelerator |
| `OutputCountingStream` | mid-chain; inner is already a non-closing wrapper (see above) |
| `SeekCountingStream` | mid-chain; inner is already a non-closing wrapper (see above) |
| `CountingReader` | mid-chain; inner is already a non-closing wrapper (see above) |

Two more own, but close themselves and tell the base to skip the second call:

| Class | Why `_SUBCLASS_CLOSES_INNER = True` |
| --- | --- |
| `_UnrarOwnedStream` | close the pipe, then reap the process, then mark closed |
| `_AcceleratorStream` | `weakref.finalize` closes the raw object once |

Those seven would need `owns_inner=True` after a flip. A missed one leaks a
handle the oracle does not pin: default `DelegatingStream` constructors wrap
`BytesIO` by the thousand and are excluded on purpose. The oracle catching the
#336 shape is not a reason to create a new silent-miss on `LockedStream`.

`subclass_closes_inner` is kept, not folded into `owns_inner`. Both values own;
the flag is the close *mechanism*. Eliminating it would double-close the
accelerator / unrar pipe, or force a new oracle key that is easy to forget.

## 3. What is left of mandatory-explicit keywords

Not much. The unusual direction is already spelled at the call site
(`owns_inner=True` on the four owning slices; later pybcj BCJ stages derive
`owns_inner=(i > 0)` in `open_folder_pipeline`). Making `owns_inner` required
on every `SlicingStream` would add `False` noise to the borrow sites the type
already describes. pyrefly/ty catching a missing kwarg converts "forgot to
think" into "typed `False` without thinking"; the oracle now fails the leak
instead.

A new `DelegatingStream` subclass is forced to pick a close contract by
`test_delegating_stream_close_inventory`, which asserts the class-level
`_SUBCLASS_CLOSES_INNER` flag (same idiom as the resume-offset inventory).
That is per-class, which is the right grain for this type.

Named constructors (`SlicingStream.owning()`) were the Parcel B analogue.
Rejected: `owns_inner` only changes `close()`, it does not switch I/O contracts
the way `lock=None` used to. A second class for that is `SharedView` already.

## 4. Reader-level flags, left alone

`SharedSource._owns_handle`, ZIP/ISO `_owned_fp`, TAR `_owned_stream` record
"did this reader open a Path." They are not wrapper kwargs. Path vs `BinaryIO`
already encodes the answer at construction. Renaming them to `owns_inner`
would collide with the wrapper vocabulary for no call-site gain.

`readinto_passthrough` shares `DelegatingStream.__init__` and is not ownership.

## 5. Leak oracle

`SlicingStream` is keyed on the **kwarg** `owns_inner`. `DelegatingStream` is
keyed on the resolved `_subclass_closes_inner` instance flag — the class-level
`_SUBCLASS_CLOSES_INNER` default, or a constructor kwarg that overrides it.
Renaming those without updating `tests/leak_oracle.py` pins nothing, and the
tests being edited are the ones that would have caught it.

Confirm the gate still fires: delete `owns_inner=True` at
`src/archivey/internal/backends/rar_reader.py` (`_bounded_member_pipe`) and run
`pytest tests/test_rar_reader.py -q --no-cov`. Expect the two
`assert inner.closed` unit tests plus the oracle's live-`unrar` errors. Restore
before committing.

## 6. Verify

```bash
uv run --no-sync pytest tests/test_stream_bases.py tests/test_slice.py \
    tests/test_leak_oracle.py tests/test_codecs.py tests/test_rar_reader.py \
    tests/test_sevenzip_reader.py::test_first_stage_bcj_does_not_close_pack_source \
    tests/test_sevenzip_reader.py::test_copy_bcj_folder_roundtrip -q --no-cov
```
