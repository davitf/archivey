# B. Do `diagnostics` and `cost` mean one thing?

The July review checked that `CostReceipt` reported honest values per backend. This
pass re-tests the **semantics**: the same axes, the same thresholds, the same answer
shape from every backend. Diagnostics semantics are in `0-diagnostics.md`; this file
holds the cross-backend evidence for both.

## Cost receipts, eight sources, one script

`main` at `878c75f`, `[all]` config, every source opened with defaults:

| Source | Format | `listing_cost` | `access_cost` | `stream_capability` | `solid_block_count` | `is_solid` | `member_count` | `format_info` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `plain.zip` | ZIP | INDEXED | DIRECT | SEEKABLE | None | False | 2 | magic / CERTAIN |
| `plain.tar` | TAR | REQUIRES_SCANNING | DIRECT | SEEKABLE | None | False | None | magic / CERTAIN |
| `plain.tar.gz` | TAR_GZ | REQUIRES_DECOMPRESSION | SOLID | SEEKABLE | 1 | True | None | content_probe / PROBABLE |
| `lz4.7z` | SEVEN_Z | INDEXED | SOLID | SEEKABLE | 1 | True | 4 | magic / CERTAIN |
| `comments.rar` | RAR | INDEXED | DIRECT | SEEKABLE | None | False | 2 | magic / CERTAIN |
| `single.gz` | GZ | INDEXED | DIRECT | SEEKABLE | None | False | 1 | magic / CERTAIN |
| `adir/` | DIRECTORY | REQUIRES_SCANNING | DIRECT | SEEKABLE | None | False | None | directory / CERTAIN |
| `BytesIO(tar.gz)`, `streaming=True` | TAR_GZ | REQUIRES_DECOMPRESSION | SOLID | SEEKABLE | 1 | True | None | content_probe / PROBABLE |

Reading across:

- **Three axes, three questions, no overlap.** Enumeration cost, layout cost, and
  source seekability are answered independently and never inferred from each other. A
  compressed tar is the one format that scores on all three (decompress to list, solid
  layout, whatever the source is), and the receipt says so without a `notes` entry.
- **`solid_block_count` and `is_solid` agree everywhere**: `None`/`False` on every
  non-solid format, `1`/`True` on the two solid ones. `is_solid` lives on `ArchiveInfo`
  and the count on the receipt, as the docstring says; a caller who wants the flag
  reads `info`, one who wants the number reads `cost`. Two homes for one fact is a
  design choice the July review accepted.
- **`member_count` is `None` exactly where listing is not free** (TAR, directory, and
  a streaming reader) and a number everywhere else. That is the honest answer, and the
  guide says so. One caveat in `B-1`.
- **`stream_capability` describes the source, not the mode.** A `BytesIO` opened with
  `streaming=True` still reports SEEKABLE, because the bytes can seek even though the
  reader will not. The `access-mode-and-cost` spec is explicit that the two are
  separate, and the streaming reader's refusals (`members()` raises
  `UnsupportedOperationError` with a message naming the three alternatives) are loud.
- **`format_info` has the same shape from every backend**, including the directory
  pseudo-archive (`directory` / `CERTAIN`), and the one probe-detected source says
  `content_probe` / `PROBABLE` rather than pretending to certainty.

`tests/test_cost_receipt.py` pins one row per format. It is the conformance assertion
the brief asked whether one exists; it does, and the July P2 fix added the RAR row.

## Findings

### B-1 · Low · `single.gz` reports `INDEXED` with one member

A single compressed file lists as `INDEXED` with `member_count=1`. It is not wrong (the
one member's name and size are known without decompressing, size being `None` where
the container does not store it), but a reader comparing it to `plain.tar`'s
`REQUIRES_SCANNING` might ask what index a gzip file has. The `ListingCost.INDEXED`
docstring names ZIP, 7z, ISO and RAR as examples and does not mention the single-file
formats.

**Recommendation.** One clause in the `INDEXED` docstring ("a single-file compressed
stream, whose one member is known from the header") so the value is explained where a
reader will look. No behaviour change.

### B-2 · Low · `ArchiveInfo.cost` and `reader.cost` are two copies of one receipt

`reader.info.cost == reader.cost` is `True`; `is` is `False`. Both are frozen, so
nothing can drift, and the `ArchiveInfo` docstring names `cost` as a field. A user who
reads one never needs the other. This is the same "two homes" pattern as `is_solid` /
`solid_block_count`, and it was accepted in July.

**Recommendation.** Nothing. Recorded so the next review does not re-raise it.

### B-3 · Diagnostics: one mechanism

Answered in `0-diagnostics.md` §What is actually fine. The evidence: one collector per
reader (`core.py:361`, created before detection so open and detect share it); every
public view is a range over it or an attachment from it; the extraction report's
per-member rows are a deliberately separate channel with a written placement rule. The
only semantic difference a caller can observe is the scope of `ExtractionReport.
diagnostics` between `extract()` and `extract_all()` (D0-2), and that is a documentation
finding.

### B-4 · `StreamCapability` and `MemberStreams`: the same answer shape

`StreamCapability` is ordered (`FORWARD_ONLY < SEEKABLE`) so
`format_availability(fmt).required_source <= reader.cost.stream_capability` is the
whole test, and the ordering is documented as "at least as strong as". The two
`MemberStreams` booleans (`seekable_members`, `concurrent_members`) are gated
identically across backends, including the directory reader, and the streaming ×
concurrent combination is refused at open. Verified by the July review and unchanged;
`test_corpus_sweep.py` still declares them per (entry × format).

## What is actually fine

- The receipt is computed at open, before any member is read, and never mutated by a
  runtime event; runtime events (a rewind that re-decompresses, a degraded seek index)
  go to diagnostics. Both halves of that rule are in the spec and held.
- `notes` was empty on all eight sources. It exists for the rare caveat, and nothing
  routine leaks into it.
- The three enums are plain `Enum`, not ordered, except `StreamCapability`, which is
  ordered for one stated reason. Consistent with the July "enums-vs-flags" reading.
