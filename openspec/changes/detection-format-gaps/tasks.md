## 0. Order

- [x] 0.0 **Implement this change first, ahead of the four other detection changes.** It is
      the smallest of the five and the only one that fixes wrong *answers* rather than wrong
      *grades*: three inputs whose own decoders accept them are currently undetectable. It
      depends on nothing and blocks nothing.

  **The far-magic reorder travels with it and cannot be split off.** Removing the LZMA Alone
  zero-dictionary guard is unsafe until far magic runs before the content probes — verified
  by lifting the guard on `main`, where a zero-system-area ISO then detects as `LZMA_ALONE`.
  The two ship together or neither does.

  **`prefixed-archive-detection` must drop its far-magic claim when it is revised**, since
  this change ships that move. Recorded in design §Sequencing rather than left to collide at
  archive time.

## 1. Red tests first

- [x] 1.1 Failing test: a zstd stream behind one skippable frame, and behind three chained
      skippable frames with differing payload sizes, detects as `ZST` (both currently raise
      `FormatDetectionError`)
- [x] 1.2 Failing test: skippable frames with no regular frame, and a skippable frame whose
      declared size exceeds the peeked prefix, are **not** claimed as zstd
- [x] 1.3 Failing test: a zlib stream at each legal window size (wbits 9–15) detects as
      `ZLIB`; six of the seven fail today
- [x] 1.4 Failing test: a zlib stream written with `FDICT` set passes the header grammar,
      and falls through on the decode — **corrected during implementation**: archivey
      supplies no preset dictionary, so the "dictionary is available" half is unreachable
      (design §2)
- [x] 1.5 Failing test: an LZMA Alone stream whose dictionary-size field is zero detects as
      `LZMA_ALONE`
- [x] 1.6 Failing test: an ISO whose 32 KiB system area holds boot-code-shaped bytes detects
      as `ISO` / `CERTAIN` / `magic`, not `BROTLI` with a fabricated member
- [x] 1.7 Regression pin (passes today, must keep passing): an ISO with a zeroed system area
      detects as `ISO` — this is what the removed Alone guard was covering

## 2. Reorder far magic ahead of the content probes

- [x] 2.1 Move the far-magic step in `_detect_format_body` to run before the content-probe
      loop, keeping its size gate (`source_byte_size()` is already computed at the probe
      step — hoist rather than recompute)
- [x] 2.2 Confirm a source shorter than the extended window takes no extended peek, and one
      of unknown length falls through on a short peek rather than erroring
- [x] 2.3 Update the module docstring in `detection.py`, which still describes the SFX scan
      as running "before the content probes" without mentioning far magic

## 3. LZMA Alone: accept a zero dictionary size

- [x] 3.1 Remove the `dict_size == 0` rejection from `_alone_header_plausible`
      (`streams/codecs.py`) and the comment claiming it guards the ISO system area
- [x] 3.2 Verify 1.5 and 1.7 both pass — the second is what proves the guard's removal is
      safe rather than merely unblocked
- [x] 3.3 **Added at implementation.** Refuse a header declaring an uncompressed size of
      exactly zero. Removing 3.1's guard on its own regressed every zero-filled source
      larger than the peeked prefix to `LZMA_ALONE` — 18 zero bytes are a valid, complete,
      *empty* Alone stream, and the bounded probe scores the run off the end of the
      trailing zeros as truncation, which is a match. Caught by
      `test_content_detection_refuses_a_zero_filled_file`; rationale, the two rejected
      alternatives and the measurements in design §5

## 4. zlib: gate on the RFC 1950 grammar

- [x] 4.1 Replace `_ZLIB_HEADERS` with a `CM == 8` / `CINFO <= 7` / mod-31 check in
      `ZlibCodec.content_probe`, accepting `FDICT`
- [x] 4.2 Assert the grammar admits exactly 66 of 65 536 `(CMF, FLG)` pairs, as a pin on the
      derivation rather than on a hand-listed set

## 5. zstd: walk skippable frames

- [x] 5.1 Add a skippable-frame walk to the zstd structural check: magic in
      `0x184D2A50 .. 0x184D2A5F`, little-endian `uint32` size, advance `8 + size`, repeat
- [x] 5.2 Require a regular `28 B5 2F FD` frame after the walk; decline on skippable-only
      input and when a declared size runs past the peeked prefix
- [x] 5.3 Keep the walk inside the already-peeked bytes — it must not trigger a larger read

## 6. Docs and sequencing

- [x] 6.1 `docs/formats.md` §Detection: replace "Magic bytes first, then extension" with the
      order the implementation has, including far magic ahead of the probes
- [x] 6.2 Note in `prefixed-archive-detection`'s design §Sequencing that its far-magic Impact
      bullet and its `Magic-first…` far-magic step are superseded here, so its revision drops
      them instead of re-shipping the move
- [x] 6.3 Close the `dev-docs/open-issues.md` / `dev-docs/IDEAS.md` references to the zstd
      skippable-frame gap and the Alone dictionary guard

## 7. Confidence assertions here are pre-ledger

- [x] 7.1 Where a test in this change asserts a `DetectionConfidence`, pin **format** and
      **`detected_by`** as the durable assertion and treat confidence as provisional:
      `detection-evidence-ledger` demotes ISO to `DISCRIMINATING_HEADER` → `PROBABLE` and
      caps any unvalidated signature at `SIGNATURE_ONLY` → `PROBABLE`, so `CERTAIN` pins
      written here would thrash when it lands
- [x] 7.2 Note the same in the fixture comments, so the later change updates them
      deliberately rather than discovering them as failures

## 8. Verify

- [x] 8.1 `uv run --no-sync pytest tests/test_detection.py tests/test_single_file.py`
- [x] 8.2 `./scripts/check.sh --fix` — green apart from the `openspec archived` leg, which
      8.5 below is what closes
- [x] 8.3 `./scripts/test.sh --all-configs` — the zstd and Brotli probes are extra-gated, so
      the `[core-only]` leg is where a skipped-probe fall-through regression would show.
      All three configurations pass
- [x] 8.4 `openspec validate --strict detection-format-gaps` — valid
- [ ] 8.5 **Archive the change** (`/openspec-archive-change`), applying the three MODIFIED
      requirements to `openspec/specs/format-detection/spec.md`. Left unchecked on purpose:
      the design moved under review — §5 replaces the Alone guard rather than removing it,
      after the plain removal regressed every zero-filled source — so the deltas should
      land in the authoritative specs once that decision is reviewed, not before. Checking
      this box is the claim that the work is done; archiving is what makes it true
