# Tasks — one source object at the boundary

> **Proposal only. Nothing here is implemented.** It starts once the borrowed-source
> streams work is on `main` (the bounded-header-allocations work already is): this change
> consolidates what those two add. Each backend moves in its own task, so a failure in the
> leak oracle or the caller-stream ownership suite names the backend that broke it.

## 1. Measure first

- [ ] 1.1 Run `benchmarks/caller_stream_probe.py` on `main` (alternating runs, shared
      `ARCHIVEY_BENCH_CACHE`) and keep the JSON as the before side.
- [ ] 1.2 Add a seekable **raw** stream shape to the probe (a `RawIOBase` over the file
      with no buffer), since that is the case this change adds a Python frame to and the
      probe's `file` and `bytesio` shapes are both already buffered.

## 2. `ArchiveSource`

- [ ] 2.1 Add `internal/source.py` with `ArchiveSource`, a read-only stream built from a
      path, a caller stream, or a `ConcatenatedFile`, choosing its full-count strategy once
      at construction (design decision 2).
- [ ] 2.2 Ownership: close the path handle, the joined volume set and its own buffer;
      detach the buffer rather than closing it; never close a caller's object (decision 3).
- [ ] 2.3 Lazy path handle, `.path`, `.volume_paths`, and a directory form with no stream
      (decision 4).
- [ ] 2.4 Size and name measured once at construction; `size` exposed only when it is a
      fact, with the fact/hint distinction recorded (decisions 5 and 6).
- [ ] 2.5 Bounded `read` and `readinto` through `read_within_reach`, clamping only on a
      fact (decision 5).
- [ ] 2.6 Move the full-count, borrow and short-read tests onto `ArchiveSource`,
      including the refusal cases and the non-seekable axis pair; add tests for lazy
      opening, the fact/hint clamp (an understating `size` attribute must not truncate),
      and a directory source refusing to read. Break each property and watch its test fail.

## 3. The boundary

- [ ] 3.1 `resolve_source` returns an `ArchiveSource` in `ResolvedSource`, building each
      caller stream in a volume list as a borrowed `ArchiveSource` inside the joined set.
- [ ] 3.2 `open_stream` and detection take the same object; `core.py`'s three
      `isinstance(source, Path)` branches read `.path` instead.
- [ ] 3.3 `SharedSource` accepts the `ArchiveSource` and stops deciding ownership itself.

## 4. Backends, one at a time

- [ ] 4.1 Directory: read `.path`; the type check becomes a check for the directory form.
- [ ] 4.2 ZIP: drop `_owned_fp`; wrap the `ArchiveSource` for measurement and the start
      offset.
- [ ] 4.3 TAR: drop the source half of `_owned_stream`; the stream-capability answer reads
      the source; the EOF-probe wrapper keeps bounding decoded input only.
- [ ] 4.4 ISO: drop `_owned_fp` and remove `_ImageBoundedStream`; the ISO header-length
      header-length tests must pass unchanged, on path and stream sources.
- [ ] 4.5 RAR: the disk-copy cost note and sibling discovery read `.path` /
      `.volume_paths`; drop `_owned_concat` where the source now owns the joined set.
- [ ] 4.6 7z and single-file: through `SharedSource` as in 3.3; single-file hands
      `.path` to the codec where it does today.

## 5. Remove what is replaced

- [ ] 5.1 Delete `BorrowedStream`, `FullCountStream`, the non-closing buffered reader and
      `ensure_full_count_reads`, and their exports.
- [ ] 5.2 Update `tests/test_stream_bases.py`'s close inventory, and drop
      `peel_for_source_size` from any class that no longer needs it.
- [ ] 5.3 Grep for every remaining `isinstance(..., Path)` on a source in `src/` and
      account for each one that stays.

## 6. Detection replay (open question — drop this group if it is declined)

- [ ] 6.1 Fold the non-seekable replay prefix into `ArchiveSource` so `open_archive` no
      longer rebinds the source to a `PeekableStream`.
- [ ] 6.2 Remove `PeekableStream` or narrow it to `detect_format`'s own public use,
      whichever the call sites leave.

## 7. Docs and spec

- [ ] 7.1 Rewrite `dev-docs/topics/stream-ownership.md`'s source section around the one
      object.
- [ ] 7.2 Update the source rows of `dev-docs/threat-model.md` to name the bounded read
      at the source.
- [ ] 7.3 Re-derive the MODIFIED `access-mode-and-cost` block from the spec as it stands
      when this change archives; the bounded source spooling change modifies the same
      requirement.

## 8. Verify

- [ ] 8.1 `./scripts/check.sh --fix` and `./scripts/test.sh --all-configs`.
- [ ] 8.2 Re-run the probe against 1.1's before side; put the table on the PR, and if the
      seekable-raw row shows, apply the bound-method fallback from design §Risks.
- [ ] 8.3 `openspec validate --strict single-archive-source`.
