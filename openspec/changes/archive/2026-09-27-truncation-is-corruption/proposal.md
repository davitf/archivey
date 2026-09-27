# Truncation is a kind of corruption

## Why

`TruncatedError` and `CorruptionError` were siblings under `ReadError`, so a caller who
wanted "the archive's bytes are bad" had to name both. The library itself caught the pair
together in 17 places. From the bytes alone a decoder often cannot tell a cut stream from
damage that decodes short, and the user guide already calls the split a best-effort guess.
After the first release, putting `TruncatedError` under `CorruptionError` would silently
widen what every `except CorruptionError` catches. So this is the cheap moment.
The maintainer, 2026-09-27: "no need to delay until after release", in a new PR.

## What Changes

- `TruncatedError` subclasses `CorruptionError` instead of `ReadError`.
  `except CorruptionError` now catches truncation; `except TruncatedError` is unchanged.
- No library catch site changes meaning. The 21 sites that catch `CorruptionError` alone
  guard in-memory parsing or backward index scans that never raise `TruncatedError`, and
  no `try` catches `CorruptionError` before `TruncatedError`.
- Every `(CorruptionError, TruncatedError)` pair in `src/` and `tests/` collapses to
  `CorruptionError`. The two sites that test `TruncatedError` before `CorruptionError` say
  that the order now matters.
- Tests that mean "damaged, not short" keep that meaning through a helper that fails on
  a `TruncatedError`.

## Impact

- Specs: `error-handling` (the hierarchy tree, one paragraph, one scenario row).
- Code: `archivey.exceptions`; `tests/corruption_util.py` and the tests using it.
- Docs: the errors table and the "best-effort guess" note in
  `docs/errors-and-diagnostics.md`.
