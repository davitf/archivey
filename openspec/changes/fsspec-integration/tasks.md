# Tasks — fsspec integration

> **Design only.** Nothing here starts before the `v0.2.0` tag. The order puts the
> benchmark first, so each later stage lands with its request counts measured against a
> committed baseline. Each numbered section is one pull request, or a few by context.

## 1. Remote benchmark axis

- [ ] 1.1 `benchmarks/remote.py`: `CountingFileSystem` over local files, recording each
      range read, size/info call and `ls` (design §Benchmark).
- [ ] 1.2 A "remote" fixture scale in `benchmarks/fixtures.py`: about 300 members and
      20 to 40 MB per archive, built at run time, for ZIP, 7z (solid and not), RAR5
      (solid and not, when the `rar` writer is present), TAR, tar.gz, tar.xz.
- [ ] 1.3 Cases: list; first, middle and last member; `extract_all()`;
      `stream_members()` under `streaming=True`. Record the four counts and
      `bytes_fetched / bytes_needed`, and print the modelled time.
- [ ] 1.4 Run against today's code with a caller-opened fsspec file (no cache, and
      fsspec's default cache) and commit `benchmarks/baselines/remote.json`, so the
      planner's gain is a reviewed baseline diff.
- [ ] 1.5 `benchmarks/remote_http.py`: the same cases over a local range server and
      `HTTPFileSystem`; fail when its request counts differ from 1.3's. Off the PR path.

## 2. `OpenFile` sources and the fetch planner

- [ ] 2.1 `open_archive()` accepts `fsspec.core.OpenFile`, alone and in sequences; the
      check runs only when `fsspec` is in `sys.modules`. Test that a `str` that looks
      like a URL makes no request and that `import archivey` imports no fsspec.
- [ ] 2.2 `RangeSource`: size once, `cat_file` fetches, doubling runs, read-through of
      short forward gaps, at most four windows (design D2). Unit-test the planner on a
      recorded read trace, independent of any format.
- [ ] 2.3 `ArchiveSource` uses `RangeSource` for an `OpenFile`; `seek_is_expensive` is
      `True`; ownership closes the opened file and nothing of the caller's.
- [ ] 2.4 Run the full corpus and the short-returning-source tests through a memory-backed
      `OpenFile`: identical listings and bytes to a path source.
- [ ] 2.5 Update the remote baseline; the diff is the evidence for the PR.

## 3. Backend range hints

- [ ] 3.1 `source.will_read(start, end)` on `ArchiveSource`, a no-op off `RangeSource`.
- [ ] 3.2 ZIP: tail hint at open, central-directory hint when larger, member span hint
      `[local header, next header or directory)`.
- [ ] 3.3 7z: next-header hint at open; pack-stream hint per member, folder start for
      solid folders.
- [ ] 3.4 ISO extent hints; RAR header-position hints during the listing walk.
- [ ] 3.5 Check the ceilings in `specs/fsspec-integration` against the baseline; update
      the baseline.

## 4. `ArchiveyFileSystem`

- [ ] 4.1 `archivey/fsspec.py` with `ArchiveyFileSystem` (design D3), and the
      `fsspec.specs` entry point in `pyproject.toml`.
- [ ] 4.2 The tree from the extraction view: reuse the code `extract_all()` uses to map a
      member to its relative path and to refuse it (DR-23a: these must agree, so share
      them). Refused members become diagnostics.
- [ ] 4.3 `info`, links, errors, read-only methods, pickling, `cachable = True`.
- [ ] 4.4 Consumer tests: pandas CSV, pyarrow Parquet, dask over two threads and over
      a process pool (pickling), a nested `archivey::archivey::` chain. These tests
      skip when the consumer package is missing; CI installs them on one leg.
- [ ] 4.5 The remote benchmark's file-system case.

## 5. Volumes over fsspec

- [ ] 5.1 Split `discover_volume_siblings` so the naming rules run on a list of names.
- [ ] 5.2 fsspec discovery through one `ls` of the parent; each volume its own
      `RangeSource`. Tests on `memory://` for all three naming schemes.

## 6. Docs

- [ ] 6.1 `new_docs/` page "Remote archives and fsspec": the two ways in, why an
      `OpenFile` beats an open file, the cache to pick when passing an open file (from
      1.5's numbers), the file system and its extraction-view tree, the RAR download.
- [ ] 6.2 IDEAS.md: replace the fsspec entry with a pointer to this change.

## 7. Later, each on its own evidence

- [ ] 7.1 Batched span fetches for `extract_all()` and `stream_members()` through
      `cat_ranges` (design D5), if the benchmark shows latency left to win.
- [ ] 7.2 RAR sparse spool investigation (design D6): measure what `unrar` reads, with
      the garbage-filled-holes test, before any code.
- [ ] 7.3 Version pinning for a changed remote object (design D7).
- [ ] 7.4 CLI URL arguments, once the maintainer answers open question 1.

## 8. Verify

- [ ] 8.1 `openspec validate --strict fsspec-integration`
