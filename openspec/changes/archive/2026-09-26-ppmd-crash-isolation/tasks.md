# Tasks — PPMd crash isolation

## 1. Decoder

- [x] 1.1 Hold compressed input in `PpmdDecoder` until the pack is complete, compressed
      EOF, or the limit is passed; decode the held member in one handover.
- [x] 1.2 Drain the output of a member handed over at `flush` (`drains_after_flush`).
- [x] 1.3 Decode members past the limit in a child process; map a dead child to
      `CorruptionError`.
- [x] 1.4 Raise `ResourceLimitError` past the limit when no child can be started.

## 2. Config and docs

- [x] 2.1 `DecoderLimits.max_ppmd_in_process_input`, validated, `None` in `UNLIMITED`.
- [x] 2.2 `docs/extracting.md`, `dev-docs/known-issues.md`, `CHANGELOG.md`.

## 3. Tests

- [x] 3.1 Random input raises and never crashes, on both paths, PPMd7 and PPMd8.
- [x] 3.2 Valid members with zero runs decode byte-exact on both paths.
- [x] 3.3 Truncated members return their prefix, then `TruncatedError`.
- [x] 3.4 No child available: `ResourceLimitError`; `None`: no child is started.
