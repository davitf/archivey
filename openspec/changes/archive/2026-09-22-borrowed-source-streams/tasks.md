# Tasks — borrowed source streams

## 1. The wrapper

- [x] 1.1 `BorrowedStream` in `streamtools/full_count.py`: forwards reads, `readinto`,
      seeks, `name`, `fileno` and `peel_for_source_size`; `close` marks only itself.
- [x] 1.2 `DelegatingStream.owns_inner` (class flag + kwarg, default `True`), honoured by
      `close`. Document why the default does not move.
- [x] 1.3 `ensure_full_count_reads` returns `BorrowedStream` for the two shapes it used to
      pass through, and is idempotent over its own wrappers.

## 2. Proof

- [x] 2.1 `tests/test_source_ownership.py`: every format from a stream, both common stream
      shapes, measurement on and off; `open_stream`, a volume sequence, a nested archive,
      and a failed open. Each case reads from the stream afterwards, not just checks
      `closed`. Red-green against the unwrapped boundary.
- [x] 2.2 Classify `BorrowedStream` in the three stream inventories, with the ownership
      group added to the close inventory and a mutation check that it fires.

## 3. Documents

- [x] 3.1 `dev-docs/topics/stream-ownership.md`: the borrow row, the `owns_inner` opt-out,
      and a correction of the §2 invariant that was never true.
- [x] 3.2 Archive this change.
