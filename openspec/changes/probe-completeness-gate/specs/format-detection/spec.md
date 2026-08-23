## ADDED Requirements

### Requirement: A content probe SHALL NOT accept an incomplete stream it can see whole

A content probe decodes a bounded prefix and treats "the decoder wants more input" as a
match, because a real stream usually continues past the prefix. When the probe knows the
source length and that length does not exceed the bytes it was handed, there is no more
input: the probe is holding the entire file. A **complete valid stream decoded in full
terminates**, so a decode that ends wanting more input SHALL be a rejection rather than a
match.

This is an invariant, not a threshold. Its false-negative set — complete valid streams
that fail to decode completely — is empty by construction, which is why it MUST NOT be
implemented as a minimum size. A 9-byte `brotli.compress(b"hello")` decodes to completion
and SHALL still be accepted.

The rule SHALL apply to every probe that decodes, not to Brotli alone: it follows from
bounded decoding rather than from any one format's framing. Measured on 66 361 real files,
it rejects **91 of 128** fabricated probe claims (71%) — 67 of them under 16 bytes — while
costing **zero** genuine streams.

The check SHALL be skipped when the source length is unknown, exactly as the framing gate
is; detection then behaves as before.

#### Scenario: completeness matrix

| Case | Expected |
| --- | --- |
| 9-byte real Brotli stream, whole file in the prefix, decodes to completion | Accepted |
| 5-byte text file whose first meta-block parses as compressed, decode wants more input | **Rejected** — the file is fully visible and does not terminate |
| Real Brotli file larger than the prefix, decode wants more input | Accepted — there genuinely is more input |
| Real Brotli file exactly the size of the prefix, decodes to completion | Accepted |
| Source length unknown (non-seekable stream longer than the peek) | Rule skipped; today's behaviour |
| LZMA Alone: 51-byte low-entropy file, fully visible, does not terminate | **Rejected** — same rule, not a Brotli special case |
| Any source larger than the prefix | Rule does not apply; other rules decide |

### Requirement: A content probe MAY follow a format's self-describing block chain

Where a format's blocks are byte-aligned and self-describing — so a successor's offset is
known without decompressing — a probe SHALL be permitted to follow that chain to test the
same framing invariant beyond the first block, and SHALL reject a link that overruns the
source or a declared end that leaves trailing bytes.

The walk exists because the first-block check goes **vacuous on large sources**: Brotli's
MLEN field tops out at 2²⁴, so past ~16 MiB every declared length fits trivially. Measured
on random blobs, the walk takes 16 MiB acceptance from 8.33% (where the first-block check
buys nothing) to 2.00%, and a `/usr` tree from 61 survivors to 14.

The walk SHALL be **bounded in the number of links** it follows. Reaching that bound means
*cannot disprove*: the probe SHALL keep the verdict the earlier rules reached and MUST NOT
reject on that basis. This is the same discipline as an unknown source length — absence of
evidence is not evidence against.

The walk stops at the first compressed block, which carries no declared length to check.
On a real Brotli file whose first meta-block is compressed — 79 of 150 in the corpus — it
therefore terminates immediately, having read four bytes.

Following the chain requires bytes at offsets that may lie past the peeked prefix. The
mechanism by which a probe reaches them is settled in this change's design; whatever it
is, the reads SHALL stay bounded and SHALL NOT decompress.

#### Scenario: chain walk matrix

| Case | Expected |
| --- | --- |
| Real `.br` file, first meta-block compressed | Walk stops at once; accepted |
| Real `.br` file, uncompressed first block, all links fit | Accepted — every declared length is honoured |
| Fabrication whose first block fits but whose second link overruns the source | **Rejected** |
| Fabrication whose chain reaches a declared end with bytes left over | **Rejected** |
| 16 MiB source whose first declared block fits trivially (MLEN ceiling) | Walk decides; first-block check alone would have accepted |
| Chain longer than the link bound | Verdict unchanged from the earlier rules; **not** a rejection |
| OLE/CFB file ≥ 7425 bytes | Still accepted — its constant magic yields a fitting chain. Known residual, unchanged |
