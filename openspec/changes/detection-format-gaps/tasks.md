## 1. Red tests first

- [ ] 1.1 Failing test: a zstd stream behind one skippable frame, and behind three chained
      skippable frames with differing payload sizes, detects as `ZST` (both currently raise
      `FormatDetectionError`)
- [ ] 1.2 Failing test: skippable frames with no regular frame, and a skippable frame whose
      declared size exceeds the peeked prefix, are **not** claimed as zstd
- [ ] 1.3 Failing test: a zlib stream at each legal window size (wbits 9–15) detects as
      `ZLIB`; six of the seven fail today
- [ ] 1.4 Failing test: a zlib stream written with `FDICT` set detects as `ZLIB` when the
      dictionary is available, and falls through when it is not
- [ ] 1.5 Failing test: an LZMA Alone stream whose dictionary-size field is zero detects as
      `LZMA_ALONE`
- [ ] 1.6 Failing test: an ISO whose 32 KiB system area holds boot-code-shaped bytes detects
      as `ISO` / `CERTAIN` / `magic`, not `BROTLI` with a fabricated member
- [ ] 1.7 Regression pin (passes today, must keep passing): an ISO with a zeroed system area
      detects as `ISO` — this is what the removed Alone guard was covering

## 2. Reorder far magic ahead of the content probes

- [ ] 2.1 Move the far-magic step in `_detect_format_body` to run before the content-probe
      loop, keeping its size gate (`source_byte_size()` is already computed at the probe
      step — hoist rather than recompute)
- [ ] 2.2 Confirm a source shorter than the extended window takes no extended peek, and one
      of unknown length falls through on a short peek rather than erroring
- [ ] 2.3 Update the module docstring in `detection.py`, which still describes the SFX scan
      as running "before the content probes" without mentioning far magic

## 3. LZMA Alone: accept a zero dictionary size

- [ ] 3.1 Remove the `dict_size == 0` rejection from `_alone_header_plausible`
      (`streams/codecs.py`) and the comment claiming it guards the ISO system area
- [ ] 3.2 Verify 1.5 and 1.7 both pass — the second is what proves the guard's removal is
      safe rather than merely unblocked

## 4. zlib: gate on the RFC 1950 grammar

- [ ] 4.1 Replace `_ZLIB_HEADERS` with a `CM == 8` / `CINFO <= 7` / mod-31 check in
      `ZlibCodec.content_probe`, accepting `FDICT`
- [ ] 4.2 Assert the grammar admits exactly 66 of 65 536 `(CMF, FLG)` pairs, as a pin on the
      derivation rather than on a hand-listed set

## 5. zstd: walk skippable frames

- [ ] 5.1 Add a skippable-frame walk to the zstd structural check: magic in
      `0x184D2A50 .. 0x184D2A5F`, little-endian `uint32` size, advance `8 + size`, repeat
- [ ] 5.2 Require a regular `28 B5 2F FD` frame after the walk; decline on skippable-only
      input and when a declared size runs past the peeked prefix
- [ ] 5.3 Keep the walk inside the already-peeked bytes — it must not trigger a larger read

## 6. Docs and sequencing

- [ ] 6.1 `docs/formats.md` §Detection: replace "Magic bytes first, then extension" with the
      order the implementation has, including far magic ahead of the probes
- [ ] 6.2 Note in `prefixed-archive-detection`'s design §Sequencing that its far-magic Impact
      bullet and its `Magic-first…` far-magic step are superseded here, so its revision drops
      them instead of re-shipping the move
- [ ] 6.3 Close the `dev-docs/open-issues.md` / `dev-docs/IDEAS.md` references to the zstd
      skippable-frame gap and the Alone dictionary guard

## 7. Confidence assertions here are pre-ledger

- [ ] 7.1 Where a test in this change asserts a `DetectionConfidence`, pin **format** and
      **`detected_by`** as the durable assertion and treat confidence as provisional:
      `detection-evidence-ledger` demotes ISO to `DISCRIMINATING_HEADER` → `PROBABLE` and
      caps any unvalidated signature at `SIGNATURE_ONLY` → `PROBABLE`, so `CERTAIN` pins
      written here would thrash when it lands
- [ ] 7.2 Note the same in the fixture comments, so the later change updates them
      deliberately rather than discovering them as failures

## 8. Verify

- [ ] 8.1 `uv run --no-sync pytest tests/test_detection.py tests/test_single_file.py`
- [ ] 8.2 `./scripts/check.sh --fix`
- [ ] 8.3 `./scripts/test.sh --all-configs` — the zstd and Brotli probes are extra-gated, so
      the `[core-only]` leg is where a skipped-probe fall-through regression would show
- [ ] 8.4 `openspec validate --strict detection-format-gaps`
