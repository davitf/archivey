# Design — probe completeness gate

Companion to `dev-docs/investigations/brotli-content-probe-results.md`, which carries the
mechanism, the analytic model, and the rejected levers. This file records only what the
two new rules are, why they are sound rather than tuned, and the one contract question the
chain walk forces.

## Rule 1 — completeness when the source is fully visible

The probe decodes a bounded prefix and asks "did the decoder complain?". Three outcomes
are possible, and today two of them count as a match:

| decoder outcome | today | why |
| --- | --- | --- |
| decoded cleanly to stream end | match | correct |
| **needs more input** | **match** | correct *only* when there is more input |
| raised a format error | no match | correct |

"Needs more input" is the right answer for a source larger than the prefix — the stream
genuinely continues past what the probe was handed. It is the *wrong* answer when the
probe is holding the entire file. A complete valid stream, handed to a decoder in full,
terminates. If it does not, the file is not a complete stream of that format.

The test is `source_length is not None and source_length <= len(prefix)`. Both values are
already in the probe's hands after `brotli-probe-framing-gate`, so this needs no new
input, no new I/O, and no interface change.

**Why it is sound.** The false-negative set is "complete valid streams that fail to decode
completely", which is empty by definition. Compare with the levers the investigation
rejected — a bigger prefix, a minimum decoded size, a WBITS allow-list — each of which
buys false positives by paying real streams roughly one-for-one. This buys 71% of the
residual for nothing.

**Measured** (`scripts/exploration/probe_residual_census.py`, 66 361 files):

| | fabrications | genuine streams |
| --- | --- | --- |
| ≤ 4096 B, fails a complete decode | **91** | 0 |
| > 4096 B | 37 | 4 |

Sixty-seven of the 91 are under 16 bytes — the population the framing check cannot touch,
because a *compressed* first meta-block declares no length to check.

**The trap to avoid.** Do not implement this as "reject small files". A
`brotli.compress(b"hello")` is 9 bytes and decodes to completion; it must survive. The
rule is about how the decode *ended*, not how big the input was.

## Rule 2 — the chain walk

Deferred from `brotli-probe-framing-gate` task 2.3 → 5.7. Uncompressed and metadata
meta-blocks are byte-aligned and self-describing, so a successor's offset is known without
decompressing anything. Walk the chain, stop at the first compressed block, and reject a
link that overruns the source or a declared end with bytes left over.

Measured then (random blobs, by source size):

| | 4 KiB | 64 KiB | 1 MiB | 16 MiB |
| --- | --- | --- | --- | --- |
| today | 8.09% | 8.38% | 6.75% | 8.33% |
| first-block check | 0.325% | 4.16% | 3.75% | 8.33% |
| chain walk | **0.050%** | **1.55%** | **1.45%** | **2.00%** |

The 16 MiB column is why this cannot be dropped: MLEN's ceiling is 2²⁴, so past ~16 MiB
every declared length fits and the first-block check is vacuous. The walk is the only form
that helps there. On a real `.br` file whose first meta-block is compressed (79 of 150 in
the corpus) the walk stops immediately, having read four bytes.

### Decision: who owns the reads — **B**, with an explicit decline signal

`compressed-streams` said a probe "MUST NOT use [the source length] to read beyond the
prefix it was given". The chain walk needs exactly that — OLE/CFB's second header sits at
~7425, past the 4096-byte prefix. Three shapes were considered:

| shape | cost | objection |
| --- | --- | --- |
| **A.** Detector runs the walk, probe stays prefix-only | detector learns Brotli framing | format knowledge leaks out of the codec, which is what the descriptor refactor removed |
| **B.** Probe gains a bounded `read_at(offset, length)` callback | one new optional parameter | widens the probe contract for every codec to serve one |
| **C.** Detector peeks a larger prefix when a probe asks | reuses `peek_more` | the ask is unbounded in principle; needs its own cap |

**Settled: B.** Optional, absent by default. Signature:

```python
read_at(offset: int, length: int) -> bytes | None
```

- full `bytes` → success
- short / empty `bytes` → EOF at that offset (walk may reject)
- `None` → caller declined; walk stops and keeps the earlier verdict (*cannot disprove*)

That distinguishes "bytes aren't there" from "I won't buffer that far" — the latter matters
for non-seekable sources, where reaching offset *N* means `PeekableStream` buffers
`[0, N)`.

**Non-seekable max offset: 1 MiB.** Enough to follow a second link after a 4- or 5-nibble
first block (MLEN up to 64 KiB / 1 MiB). A 6-nibble first block (up to 16 MiB) is the
known compromise: `read_at` returns `None`, walk cannot disprove. Seekable sources seek;
the 1 MiB cap does not apply to them.

### Link-cap and byte-fetch semantics

Hitting either budget means **cannot disprove**, so the probe keeps whatever verdict the
earlier rules reached — it does not reject. That is the same discipline as an unknown
source length: absence of evidence is not evidence.

| Bound | Value | Notes |
| --- | --- | --- |
| Max links | **8** | Real-tree census on this image: among 2 832 first-block acceptors, every chain rejection happened by link index ≤ 1; live probe hits never walked past 1. Random blobs plateau by 4. **Revisit with hard data if a future corpus shows deeper rejecting chains.** The survey's 64 was a resource-guard default, not a measured optimum. |
| Max offset (non-seekable only) | **1 MiB** | Memory-governing forward-only ceiling for `read_at` (buffers `[0, offset)`). Declared in the live requirement; the former 4 KiB "bytes fetched" counter was dead code under the link cap and measured the wrong cost. |

## What this change deliberately does not do

- **No confidence changes.** Whether a probe-only hit is `PROBABLE` or `GUESS`, and
  whether a decode failure is stamped `format_unconfirmed`, belong to
  `probe-provenance-unconfirmed`. Note the interaction: this change removes 91 of the 128
  fabrications, which shrinks the population that sibling change is arguing about. Land
  this one first and re-run the census before sizing it.
- **No magic denylist.** OLE/CFB survives both rules by construction (its constant 8-byte
  magic always declares a fitting MLEN). That was declined in `brotli-probe-framing-gate`
  with the reason recorded, and nothing here reopens it.
- **`brotli-probe-framing-gate` is archived and synced**, in the same PR that opens this
  change. Its requirements are therefore live text, and these deltas edit shipped wording
  rather than a pending block. That is also why this change MODIFIES the framing
  requirement rather than only ADDing beside it: the deferral paragraph pointed at a task
  list that now lives inside an archive directory. See tasks 0.1–0.2.
