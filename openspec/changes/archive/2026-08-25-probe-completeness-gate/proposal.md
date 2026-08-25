# Finish the probe soundness work: demand completeness, then walk the chain

## Why

`brotli-probe-framing-gate` (#255/#261) shipped the **first-block** framing check: a
declared meta-block length must fit inside the source. It cut the Brotli probe's
real-world false-positive rate from 3.5% of a `/usr` tree to ~0.19%. Two measured gaps
remain, and both are the same kind of rule as the one that landed — a consistency check
the file itself implies, not a threshold anyone chose.

Re-measured on this image with `scripts/exploration/probe_residual_census.py`
(66 361 files under `/usr/lib`, `/usr/bin`, `/usr/share`, `/usr/local`):

| | count | share of tree |
| --- | --- | --- |
| files a content probe claims | 132 | 0.199% |
| of those, **genuine** streams | 4 | — |
| of those, **fabricated** | **128** | 0.193% |

**Gap 1 — the probe tolerates truncation even when it can see the whole file.** The probe
decodes a bounded prefix and treats "ran out of input" as a match, which is right for a
source larger than the prefix. When the source is *no larger* than the peeked prefix, the
probe is holding the entire file, and running out of input means the stream does not
terminate — it is not a complete valid stream. **91 of the 128 fabrications (71%) are files
that fit entirely inside the 4096-byte prefix and fail a complete decode.** Sixty-seven of
them are under 16 bytes. Cost to genuine streams: **zero** — a complete valid stream
decodes to completion by definition, and the four real ones in this corpus are all larger
than the prefix anyway.

**Gap 2 — the first-block check goes vacuous on large sources.** MLEN's ceiling is 2²⁴, so
past ~16 MiB any declared length fits trivially. The chain walk (deferred as task 5.7 of
`brotli-probe-framing-gate`) follows byte-aligned self-describing meta-blocks, whose
successor offsets are known without decompressing. Measured then: 61 → 14 survivors on a
`/usr` tree, and 8.33% → 2.00% on 16 MiB random blobs where the first-block check does
nothing at all.

The two compose cleanly and cover disjoint populations: completeness handles sources at or
below the prefix, the chain walk handles sources above it.

## What Changes

- **Completeness when the source is fully visible.** When the probe knows the source
  length and it does not exceed the bytes it was handed, a decode that ends in "needs more
  input" SHALL be a rejection rather than a match. Applies to every probe that decodes,
  not just Brotli — it is a property of bounded decoding, not of Brotli's framing.
- **Chain walk** (from `brotli-probe-framing-gate` task 5.7): follow the chain of
  byte-aligned self-describing meta-blocks, bounded in link count, stopping at the first
  compressed block; reject a link that overruns the source or a declared end with trailing
  bytes.
- **Decide who owns the reads.** The chain walk needs bytes at successor offsets that
  often lie past the peeked prefix (OLE/CFB's next header sits at ~7425). Today's contract
  says a probe MUST NOT read beyond its prefix. This change settles that: see `design.md`.
- **No confidence changes, no new error behaviour.** Those live in the sibling change
  `probe-provenance-unconfirmed`.

## Impact

- Modules: `src/archivey/internal/streams/codecs.py` (`BrotliCodec.content_probe`, and the
  shared completeness rule for the zlib and LZMA Alone probes),
  `src/archivey/internal/detection.py` if the detector ends up owning the chain reads.
- Public API: more junk raises `FormatDetectionError` where it used to produce a
  fabricated single-file member. That is the point, and it is the same direction #261
  already moved. No signature change is required for completeness — the probe already
  receives both the prefix and `source_length`, and `source_length <= len(prefix)` is the
  whole test. The chain walk may need a bounded read-at callback; that is the open design
  question.
- Tests: the real-stream corpus from `brotli-probe-framing-gate` task 4.1 stays the
  binding zero-false-negative constraint, extended with **small** real streams (a
  `brotli.compress(b"hello")` is 9 bytes and must survive completeness).
- Docs: `docs/formats.md` already describes detection as content-probe + framing gate;
  extend that sentence rather than adding a section.
- Related: shrinks the residual registered as `dev-docs/open-issues.md` P12 and
  `dev-docs/threat-model.md` O10 again, without closing it — the OLE/CFB and COFF families
  above the prefix still survive both rules.

## Capabilities

### New Capabilities

### Modified Capabilities

- `format-detection` — a new normative requirement that a probe must not accept an
  incomplete stream when it can see the whole source, plus the chain walk as a SHALL
  rather than the MAY the framing requirement currently allows.
- `compressed-streams` — only if the chain walk lands as a probe-side callback; the
  completeness rule needs no interface change.

## Decisions

- **Completeness before the chain walk.** Completeness is 71% of the residual, needs no
  new I/O, no interface change, and no design question answered. The chain walk is the
  smaller win, needs a contract decision, and only helps above the prefix. If this change
  has to be split, completeness ships alone and the chain walk follows.
- **Not a threshold.** Neither rule has a tunable number in it. "Does the file terminate"
  and "does each declared block fit" are questions the file answers about itself. The
  link-count bound on the walk is the one exception, and it is a resource guard whose
  hitting means *cannot disprove*, not *reject* — see `design.md`.
- **Applies to all probes, not just Brotli.** Two of the four LZMA Alone fabrications are
  under the prefix. Scoping completeness to Brotli would leave a rule that is true of
  bounded decoding generally sitting in one codec.
