# Philosophy

> End-user framing of what Archivey is for. The full maintainer vision (adoption
> strategy, quality scaffolding, non-goals) lives in the repository root as
> `VISION.md`.

## One sentence

**The default Python library for archives** — read, inspect, stream, or safely extract
any common format through one interface, the way `requests` became the default for HTTP.

## Simple API

One opener, one reader shape, one member model:

```python
with archivey.open_archive(path) as reader:
    for member in reader:
        ...
```

Format differences show up as **data** (`None`, documented sentinels, cost receipts) —
not as silent guesses or a different API per backend.

## Safe by design

Extraction cannot be zip-slipped, symlink-escaped, or decompression-bombed unless you
explicitly opt out. Safety is a contract, not a marketing flag. See
[Safe extraction](extracting.md).

## Don’t-shoot-yourself by design

Archive formats hide expensive operations: seeking inside a compressed stream can
re-decompress from the start; opening members out of order in a solid archive can
decode the same block repeatedly; concurrent member streams need real coordination.

Archivey’s defaults are the **cheap, honest path**:

- forward-only member streams, one live stream at a time
- no seek indexes or accelerators until you ask (`seekable_members=True`)
- no concurrent opens until you ask (`concurrent_members=True`)
- random-access open fails fast on a non-seekable source (no silent buffering)

When you need more, you **declare** it. Escape hatches are explicit, not ambient. See
[Access costs and pitfalls](access-and-cost.md).

## Escape hatches for advanced use

| Need | How |
| --- | --- |
| Pipes / sockets | `open_archive(..., streaming=True)` — TAR and the single-file compressors; ZIP, ISO, 7z and RAR need a seekable source in either mode |
| Seek inside a member | `seekable_members=True` |
| Many open members / workers | `concurrent_members=True` |
| Trusted / unlimited extract | `ExtractionPolicy.TRUSTED`, `ExtractionLimits.UNLIMITED` |
| Tune accelerators / EOF / listing caps | `ArchiveyConfig` (`listing_limits`, …) |

## Content-first, not extraction-first

Reading, streaming, and metadata are the primary surface. Extraction is first-class but
second in priority. Writing is a natural extension and may land after a “reads everything”
1.0.

## Honest about damage and cost

Wrong extensions, truncated archives, and solid blocks are normal. Identification is
evidence-based (magic first). Access cost is queryable (`reader.cost`). Prefer
`stream_members()` when order matters. Prefer stored hashes (`member.hashes`) when you
only need integrity fingerprints.

Wall-time expectations are **aspirational peer-ratio bands** (not a silent promise):
see [Access costs — wall-time bands](access-and-cost.md#wall-time-bands-aspirational). Re-run the
harness if you want numbers on your machine.

## What this is not

- Not an everything-tool (no in-place modify, no async in v1)
- Not a backup engine by itself
- Not a compatibility shim for `zipfile` / `tarfile` / `py7zr` APIs — one clean API, with
  a [migration guide](migrating.md) rather than a drop-in replacement
