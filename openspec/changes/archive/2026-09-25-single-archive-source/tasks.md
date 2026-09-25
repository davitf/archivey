# Tasks — one source object at the boundary

> **Implemented** except 7.3, which runs when the change archives. It consolidates what
> the borrowed-source-streams and bounded-header-allocations work added, both on `main`.
> Each backend moves in its own task, so a failure in the leak oracle or the
> caller-stream ownership suite names the backend that broke it.

## 1. Measure first

- [x] 1.1 Run `benchmarks/caller_stream_probe.py` on `main` (alternating runs, shared
      `ARCHIVEY_BENCH_CACHE`) and keep the JSON as the before side.
- [x] 1.2 Add a seekable **raw** stream shape to the probe (a `RawIOBase` over the file
      with no buffer), since that is a case this change adds a Python frame to and the
      probe's `file` and `bytesio` shapes are both already buffered.
- [x] 1.3 Make `path` a treatment shape in the probe's `--compare`, and take the noise
      control from the odd/even split of each side's own runs instead: this change
      touches path sources, so `path` can no longer be the untouched control.

## 2. `ArchiveSource`

- [x] 2.1 Add `internal/source.py` with `ArchiveSource`, a read-only stream built from a
      path, a caller stream, or a `ConcatenatedFile`, choosing its full-count strategy once
      at construction (design decision 2).
- [x] 2.2 Ownership: close the path handle, the joined volume set and its own buffer;
      detach the buffer rather than closing it; never close a caller's object (decision 3).
- [x] 2.3 Lazy path handle, `.path`, `.volume_paths`, and a directory form with no stream
      (decision 4).
- [x] 2.4 Size and name measured once at construction; `size` exposed only when it is a
      fact, with the fact/hint distinction recorded (decisions 5 and 6).
      *As built:* `size` is the fact; the caller's hint is `size_hint`, read by
      `compressed_source_size`, its complement (whether a byte counter stands in) and
      detection's total size, none of which bounds a read. A first build exposed the
      hint as `size`, and slices and shared views over the source clamped on it.
- [x] 2.5 Bounded `read` and `readinto`: `read_within_reach` decides how many bytes may be
      requested and runs over the full-count strategy, never over the raw inner; clamp
      only on a fact (decision 5). Test that a one-byte-chunk non-seekable source still
      returns `n` through the bound.
- [x] 2.6 Move the full-count, borrow and short-read tests onto `ArchiveSource`,
      including the refusal cases and the non-seekable axis pair; add tests for lazy
      opening, the fact/hint clamp (an understating `size` attribute must not truncate),
      and a directory source refusing to read. Break each property and watch its test fail.

## 3. The boundary

- [x] 3.1 `resolve_source` returns an `ArchiveSource` in `ResolvedSource`, building each
      caller stream in a volume list as a borrowed `ArchiveSource` inside the joined set;
      the parts do not bound, the outer source does, and the joined size is a fact when
      every part's is (decision 7a).
      *As built:* the part wrappers were removed; the join gathers and borrows its
      stream parts itself.
- [x] 3.2 `open_stream` and detection take the same object; detection over a path opens
      and closes its own handle from `.path`; `core.py`'s three `isinstance(source, Path)`
      branches read `.path` instead. Standalone `detect_format` is unchanged.
- [x] 3.3 `SharedSource` accepts the `ArchiveSource` and stops deciding ownership itself.
- [x] 3.4 The zero-origin view stays a `SlicingStream` over the `ArchiveSource`
      (decision 7); check it clamps and borrows as the spec's wrapper rule requires.
      *As built:* the `SlicingStream` sits *inside* the source
      (`rebase_to_current_position`), between it and its reader, so detection and the
      backend keep holding one object and its facts (`.path`, `.size`) move to the new
      origin with it. The slice borrows, and the source's own clamp counts from the new
      origin (`test_a_rebased_source_clamps_from_its_new_origin`).

## 4. Backends, one at a time

- [x] 4.1 Directory: read `.path`; the type check becomes a check for the directory form.
- [x] 4.2 ZIP: hand `zipfile` `.path` as today when neither measurement nor a start offset
      needs a handle (decision 4); otherwise the `ArchiveSource`, wrapped for measurement
      and the start offset. Drop `_owned_fp`.
- [x] 4.3 TAR: drop the source half of `_owned_stream`; the stream-capability answer reads
      the source; the EOF-probe wrapper keeps bounding decoded input only.
- [x] 4.4 ISO: drop `_owned_fp` and remove `_ImageBoundedStream`; the ISO header-length
      tests must pass unchanged, on path and stream sources.
- [x] 4.5 RAR: the disk-copy cost note and sibling discovery read `.path` /
      `.volume_paths`; drop `_owned_concat` where the source now owns the joined set.
      *As built:* `_owned_concat` stays for one case, siblings RAR discovers from volume
      1's path; the boundary hands RAR that path, not a join (see 5.3).
- [x] 4.6 7z and single-file: through `SharedSource` as in 3.3; single-file hands
      `.path` to the codec where it does today.

## 5. Remove what is replaced

- [x] 5.1 Delete `BorrowedStream`, `FullCountStream`, the non-closing buffered reader and
      `ensure_full_count_reads`, and their exports.
      *As built:* the non-closing buffered reader stays; it is what `ensure_bufferedio`
      returns, and the source's buffer over a seekable raw stream is one. The
      `DelegatingStream` `_OWNS_INNER` opt-out, added only for `BorrowedStream`, went
      with it.
- [x] 5.2 Update `tests/test_stream_bases.py`'s close inventory, and drop
      `peel_for_source_size` from any class that no longer needs it.
- [x] 5.3 Grep for every remaining `isinstance(..., Path)` on a source in `src/` and
      account for each one that stays.
      *As built:* the backends read `.path`, `.is_directory` and `.joined`. What stays is
      input handling before the source exists (`resolve_source`, the stub follower in
      `detection.py`), and RAR's sibling join from volume 1's path.

## 6. Detection replay (design decision 8)

- [x] 6.1 Fold the non-seekable replay prefix into `ArchiveSource` so `open_archive` no
      longer rebinds the source to a `PeekableStream`.
- [x] 6.2 The detection workspace and `open_stream` peek the `ArchiveSource`; the
      single-file backend's reuse of the detection prefix reads it from there.
- [x] 6.3 Remove `PeekableStream`, or record why a call site keeps it. Removed;
      `DETECTION_LIMIT` moved to `detection_workspace.py`.

## 7. Docs and spec

- [x] 7.1 Rewrite `dev-docs/topics/stream-ownership.md`'s source section around the one
      object.
- [x] 7.2 Update the source rows of `dev-docs/threat-model.md` to name the bounded read
      at the source.
- [x] 7.3 Re-derive the MODIFIED blocks (`access-mode-and-cost`, `format-detection`,
      `testing-contract`, `backend-registry`, `compressed-streams`) from the specs as
      they stand when this change archives. The `bounded-source-spooling` change
      modifies the same `access-mode-and-cost` requirement, so whichever archives
      second re-derives from the first; this is left open until then rather than
      archived with the implementation.
      *As built:* `compressed-streams` gained a delta that re-points its full-count
      requirement's `streamtools.ensure_full_count_reads` parenthetical at the
      `ArchiveSource`, so archiving closes it with the rest.
      *Archived first (2026-09-25):* each MODIFIED block was diffed against the main spec
      at archive time and differs only in the lines this change meant to change.
      `bounded-source-spooling`, still open, re-derives its `access-mode-and-cost` block
      from the result.

## 8. Verify

- [x] 8.1 `./scripts/check.sh --fix` and `./scripts/test.sh --all-configs`.
- [x] 8.2 Re-run the probe against 1.1's before side; put the table on the PR, with the
      path rows read as treatment. If the seekable-raw row shows, apply the bound-method
      fallback from design §Risks; if a path row shows, say which backend and why.
      *As built:* the first run showed path and seekable-raw sources about 4% slower,
      from a Python frame on every read. `read` now inlines the clamp for a
      fact-length source (about 0.7 µs per read left over the bare handle, from
      1.1 µs); the bound-method fallback is not available, because the clamp has to
      run. The table on the PR is the second run.
- [x] 8.3 `openspec validate --strict single-archive-source`.
