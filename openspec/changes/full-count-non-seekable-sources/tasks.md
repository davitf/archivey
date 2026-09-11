## Implementation notes

Red before green: task 1.1 is the failing test, and it must be watched failing before
`FullCountStream` exists. The double is the point of the change — the gap survived
because no existing double is short-returning *and* non-seekable.

Landable as one PR. `design.md` §Open question may split task 4.1 out if
`ConcatenatedFile` needs its own handling.

## 1. Red — prove the gap

- [ ] 1.1 Add `ShortReadNonSeekable` to `tests/streams_util.py`: caps both `read` and
  `readinto` at `max_chunk`, `seekable()` is `False`, `seek()` raises
  `io.UnsupportedOperation`. Docstring says why neither existing double covers this
  (`ShortReadBytesIO` is seekable; `NonSeekableBytesIO` delegates to `BytesIO`).
- [ ] 1.2 Assert the boundary directly, not a backend:
  `ensure_full_count_reads(ShortReadNonSeekable(data, 1)).read(n)` returns `n` bytes.
  Watch it fail on `main` — the source is returned unchanged, so it returns 1 byte.
- [ ] 1.3 Assert exact consumption: after `read(n)`, exactly `n` bytes have been taken
  from the underlying source (count them in the double). This is the assertion that
  would fail if someone later "fixes" 1.2 with a `BufferedReader`.

## 2. Green — the wrapper

- [ ] 2.1 Add `FullCountStream(ReadOnlyIOStream)` to
  `internal/streams/streamtools/binaryio.py`. `read(n)` delegates to `read_exact` for
  `n >= 0` and drains to EOF for `n < 0`; no buffer; `seekable()` stays `False`.
  Docstring carries `design.md` D2/D3 inline: why no read-ahead, and why `read_exact`
  rather than `read_full_count` (ADR 0014's three gather policies).
- [ ] 2.2 `ensure_full_count_reads` wraps a non-seekable source in `FullCountStream`
  instead of returning it unchanged. Replace the docstring's non-seekable paragraph —
  the current justification is wrong (see 5.1); cite ADR 0010 for what the rule actually
  forbids.
- [ ] 2.3 Confirm 1.2 and 1.3 pass; revert 2.1–2.2 and watch them fail again; restore.

## 3. Simplify the now-redundant gather

- [ ] 3.1 `PeekableStream._fill_to`: collapse the `while` loop to a single `read` now
  that the inner is full-count. Comment why the loop is gone and what guarantees it.
- [ ] 3.2 Confirm the composition order is `PeekableStream(FullCountStream(raw))` at
  both wrap sites in `core.py` (detection path in `open_archive`, and `open_stream`).
- [ ] 3.3 `PeekableStream` stays — `peek` pushback is not what `FullCountStream` does.
  Do not delete it or fold the two.

## 4. Coverage

- [ ] 4.1 Each streaming-capable format from `ShortReadNonSeekable(max_chunk=1)`,
  **with and without** explicit `format=`, parity-asserted against the full-count open.
  The explicit-`format=` case is the one that skips `PeekableStream`.
- [ ] 4.2 Plain uncompressed TAR is a required case: it is the path where only stdlib
  `tarfile._Stream` buffering stands between a short-returning pipe and a bogus
  corruption report today.
- [ ] 4.3 Resolve `design.md` §Open question for `ConcatenatedFile` (non-seekable volume
  items): covered by its own gather, or needs the wrapper per item. Add the case either
  way.
- [ ] 4.4 Run all three dependency configs before pushing (`[all]`, `[all-lowest]`,
  `[core-only]`) per `CONTRIBUTING.md`.

## 5. Docs and specs

- [ ] 5.1 Remove the wrong justification wherever it survives. `ensure_full_count_reads`
  has claimed, in every revision so far, that buffering a non-seekable source would make
  it seekable. `io.BufferedReader.seekable()` forwards to the raw, so it would not.
- [ ] 5.2 Note the `streamtools` layering win in the module docstring: the boundary now
  supplies its own guarantee rather than depending on `PeekableStream` (a layer above) or
  `tarfile` internals.
- [ ] 5.3 `openspec validate --strict full-count-non-seekable-sources`
- [ ] 5.4 `./scripts/check.sh --fix` and `./scripts/test.sh` clean.
- [ ] 5.5 `openspec archive full-count-non-seekable-sources --yes` in the finishing PR;
  commit the resulting `openspec/specs/` diff.
