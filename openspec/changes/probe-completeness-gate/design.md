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

### The open question this change must settle: who owns the reads

`compressed-streams` says a probe "MUST NOT use [the source length] to read beyond the
prefix it was given". The chain walk needs exactly that — OLE/CFB's second header sits at
~7425, past the 4096-byte prefix. Three shapes:

| shape | cost | objection |
| --- | --- | --- |
| **A.** Detector runs the walk, probe stays prefix-only | detector learns Brotli framing | format knowledge leaks out of the codec, which is what the descriptor refactor removed |
| **B.** Probe gains a bounded `read_at(offset, length)` callback | one new optional parameter | widens the probe contract for every codec to serve one |
| **C.** Detector peeks a larger prefix when a probe asks | reuses `peek_more` | the ask is unbounded in principle; needs its own cap |

**Recommendation: B**, with the callback optional and absent by default, so a probe that
does not take it behaves exactly as today. It keeps Brotli's framing knowledge inside
`BrotliCodec` — the reason `content_probe` is a codec method at all — and the bound is
explicit in the signature rather than implied. A is cheaper to write and worse to live
with; C hides the bound.

Not settled here because it is a contract move that deserves the implementer's eye on the
actual call sites. Whichever is chosen, the walk MUST stay bounded in link count.

### Link-cap semantics

Hitting the cap means **cannot disprove**, so the probe keeps whatever verdict the earlier
rules reached — it does not reject. That is the same discipline as an unknown source
length: absence of evidence is not evidence. The survey script used 64 links; that is a
starting point, not a measured optimum.

## What this change deliberately does not do

- **No confidence changes.** Whether a probe-only hit is `PROBABLE` or `GUESS`, and
  whether a decode failure is stamped `format_unconfirmed`, belong to
  `probe-provenance-unconfirmed`. Note the interaction: this change removes 91 of the 128
  fabrications, which shrinks the population that sibling change is arguing about. Land
  this one first and re-run the census before sizing it.
- **No magic denylist.** OLE/CFB survives both rules by construction (its constant 8-byte
  magic always declares a fitting MLEN). That was declined in `brotli-probe-framing-gate`
  with the reason recorded, and nothing here reopens it.
- **No archive of `brotli-probe-framing-gate`.** That change is deliberately still
  in-flight (its D7 → A). See tasks 0.1–0.2 for how these deltas relate to its unarchived
  requirement.
