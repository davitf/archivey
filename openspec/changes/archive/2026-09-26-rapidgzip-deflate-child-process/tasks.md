# Tasks — rapidgzip DEFLATE family in a child process

## 1. Measure

- [x] 1.1 `scripts/bench_rapidgzip_child.py`: child start-up, full read in-process vs pipe
      vs shared memory, seek round trip, stdlib baseline.

## 2. Child

- [x] 2.1 `rapidgzip_worker.py`: framed protocol, pump thread, parent-served source.
- [x] 2.2 `rapidgzip_child.py`: `RapidgzipChildStream` (read/seek/tell/rewind probe/close),
      parked source faults, death classification, read-ahead buffer.
- [x] 2.3 `child_exit.py`: crash / SIGKILL / other classification.
- [x] 2.4 `codecs.py`: gzip, zlib and deflate open through the child; `AUTO` skips rapidgzip
      where no child can run; spawn failure is `ResourceLimitError`; child-reported
      `RuntimeError` translates to `CorruptionError`.

## 3. Tests

- [x] 3.1 Crash harness: every codec × path / file / `BytesIO` × six access patterns, and
      `open_archive(seekable_members=True)` on a truncated `.gz`, exit cleanly with an
      archivey error. The raw-rapidgzip canary still aborts.
- [x] 3.2 Death by signal, caller-source exceptions and interrupts, no child available,
      random read/seek sequences, background source reads.

## 4. Docs

- [x] 4.1 `docs/access-and-cost.md`, `docs/gotchas.md`, `dev-docs/known-issues.md`,
      `dev-docs/investigations/rapidgzip-upstream-report.md`, `CHANGELOG.md`.

## 5. Archive

- [x] 5.1 `openspec archive rapidgzip-deflate-child-process --yes`.
