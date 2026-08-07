# Vocabulary / CLI / concept count

## F8 — `MemberStreams` vs `open_stream(seekable=)` (CONFIRMED)

Same seekability concept, two spellings:

- `open_archive(..., seekable_members=True, concurrent_members=True)` →
  `MemberStreams` flags
- `open_stream(..., seekable=True)`

Freeze-cost if left at `0.2.0`. Options: rename `open_stream` kw to
`seekable_members`, alias both, or document as intentional stream-vs-archive
wording. See QUESTIONS Q-vocab.

## F11 — Extras install hints (CONFIRMED fine)

Live `src/` hints:

- `codecs.py` — PPMd / Deflate64 / zstd / lz4 / brotli →
  `pip install archivey[recommended]`
- `crypto.py`, `sevenzip_pipeline.py` (pybcj), ISO → `[recommended]`
- rapidgzip → `[seekable]`

No leftover `install archivey[7z]` in library code. Older `review/docs/*`
inventory text is historical, not runtime.

## F9 — CLI defaults vs library (CONFIRMED; accept)

| Knob | Library | CLI |
|---|---|---|
| Overwrite | `ERROR` | `rename` (`cli/main.py:272–273`) |
| OnError | `STOP` | `CONTINUE` (`extract_cmd.py:422`; `--stop-on-error` restores STOP) |
| Dest | Caller path | Smart anti-tarbomb (`extract_cmd.py` smart_dest) |
| Exit 3 | n/a | Policy-only blocks (`exit_codes.py`) |

**Authority:** `review/archive/2026-07-20-cli-product/QUESTIONS.md` Q1 —
deliberate product split for the safer-unzip demo. must-explain **#23**.
**Vehicle:** accept as format/product law with citation; ensure `docs/cli.md`
keeps the divergence loud (Topic 8 if thin).

## F7 — Stale must-explain #25 (CONFIRMED docs)

Still says directory path forces DIRECTORY even if `format=` says otherwise.
`#225` made that an `ArchiveyUsageError`. **Vehicle:** docs-only edit to
`must-explain.md` / any guide echo.

## F14 — Concept count

| Page | Format-conditionals (heuristic) |
|---|---:|
| gotchas.md | ~7 |
| opening-and-listing.md | ~11 |
| reading-members.md | ~1 |

29 must-explain behaviours. Consistency-flavoured IDs: 4, 9, 10, 11, 13, 16,
21, 23, 25. This is the measurable "how many concepts does the common task
cost?" signal — not a defect list by itself. Paying F1/F2/F3/F6 reduces
future Gotchas; F8/F9 are vocabulary/product.

## CLI import spelling (brief §C)

`extract_cmd.py` imports `ExtractionProgress` from public `archivey`;
`progress.py` / `test_cmd.py` from `archivey.internal.extraction_types`.
Trivial; type is public. Optional cleanup — not a freeze blocker.
