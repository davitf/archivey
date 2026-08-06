## 1. Close streams on reader close

- [x] 1.1 Weak registry of public member streams on the reader (weak, so it is not one
      more thing keeping a dropped stream alive)
- [x] 1.2 `_close_public_streams()` — snapshot, skip already-closed, close in open
      order, collect failures
- [x] 1.3 Call it **after** the reader transitions to closed, so a `close()` that
      raises on an active pass leaves streams untouched
- [x] 1.4 One failure propagates; several surface as a `BaseExceptionGroup`
- [x] 1.5 Confirm teardown still runs once, after the last lease drops — the source is
      never closed underneath a live stream

## 2. Fix the finalizer that could never fire

- [x] 2.1 `release_live_stream_id(sid)` on `ReaderState`; `release_live_stream` keeps
      its signature and delegates
- [x] 2.2 `_register_public_stream`'s callback captures `id(stream)`, not `stream`
- [x] 2.3 Verify: a dropped stream is collected and its fd released after `gc.collect()`

## 3. Tests

- [x] 3.1 Invert the six tests asserting the escaped-stream contract
- [x] 3.2 `test_dropped_stream_is_collected_without_reader_close` — the regression
      guard; **verified it fails without the fix**
- [x] 3.3 `test_dropped_stream_is_garbage_collected` — the reader-close path (passes
      either way, since closing detaches the finalizer; kept as behaviour, not guard)
- [x] 3.4 Rework `test_read_after_reader_and_source_close_raises_typed_error` to reach
      the typed-error path via the caller closing the source, which is now the only
      way to reach it
- [x] 3.5 Rework the dual-failure `ExceptionGroup` test: both halves now fail during
      one `reader.close()`

## 4. Spec and docs

- [x] 4.1 Scope the gate's "never silently close" sentence to contention
- [x] 4.2 Invert the lifecycle requirement + five matrix rows
- [x] 4.3 `docs/reading-members.md` states the behaviour
- [x] 4.4 `docs/migrating.md` — stdlib parity now holds, so check nothing claims otherwise
- [x] 4.5 Close **P7** in `dev-docs/open-issues.md`

## 5. Verify

- [x] 5.1 `openspec validate --strict close-member-streams-on-reader-close`
- [x] 5.2 Dry-run archive on a scratch tree; confirm `~2`, then reset
- [x] 5.3 Suite green in all three dependency configurations
- [x] 5.4 `ruff`, `pyrefly`, `mkdocs build --strict`
