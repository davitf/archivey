## MODIFIED Requirements

### Requirement: A content probe SHALL NOT accept framing the source cannot hold

RFC 7932 lets a Brotli meta-block *declare* a length and then emit literal bytes: a
non-last uncompressed meta-block is a four-byte header after which the decoder copies.
A bounded-prefix decode therefore cannot distinguish a real stream from any data whose
first bytes happen to parse as such a header — measured at **8.2% of arbitrary binary
data** and **3.5% of a real `/usr` tree**, the latter dominated by files opening `/**\n`.

A **complete, valid** stream always satisfies
`header_bytes + declared_length <= source_length` for a declared (uncompressed or
metadata) meta-block, because those bytes must physically be present. When the source
length is known, the Brotli probe SHALL reject a prefix whose **first** declared
meta-block violates that invariant. Detection supplies that length via the existing
cheap size probe (`source_byte_size`); when the length is unknown the check is skipped,
not guessed, and detection behaves as before.

A stronger **chain walk** — following byte-aligned self-describing meta-blocks and
rejecting a later link that overruns, or a declared end with trailing bytes — is
required by its own requirement, *A content probe MAY follow a format's self-describing
block chain*, which supersedes the deferral this paragraph used to record. This
requirement remains satisfied by the first-block check alone; the walk is what covers
sources large enough for the first-block check to go vacuous.

The same first-block principle SHALL apply to the **LZMA Alone** probe, whose only
measured real-world false positives are files that are *exactly* its 13-byte header: a
source no longer than the header carries no range-coder payload and cannot be an Alone
stream. Rejecting those removed 4 of 4 measured hits across 40 000 real files. This is
the same invariant, not a second heuristic — the framing a source declares must fit what
it holds.

No decompression beyond today's bounded prefix is required for the first-block check.

This requirement is about *soundness*, not tuning: it MUST NOT reject any complete valid
stream, so the real-stream corpus in `testing-contract` is the binding constraint.
Probe-parameter tuning (larger prefix, minimum decoded output, WBITS whitelists) SHALL
NOT be used in its place — each was measured to trade false positives for false negatives
roughly one-for-one, or to reject real `.br` files.

That distinction — a threshold traded against a false-positive rate, versus a check the
format's own framing already implies — is what *Executable-looking prefixes must not
silently become a wrong stream format* is stating when it forbids "tightening the Brotli
probe". The two requirements stand together; this one is the invariant, that one is the
prohibition on knobs.

#### Scenario: framing gate matrix

| Case | Expected |
| --- | --- |
| Real `.br` file whose first meta-block is compressed | Accepted (no declared length to check) |
| Real `.br` file whose first meta-block is uncompressed (incompressible payload) | Accepted — declared length fits by construction |
| `MZ` + `\x90`×4094 (declares 2 171 061 bytes, file is 4096) | Rejected — declared framing overruns the source |
| A `/**\n…` C header (declares an uncompressed block past EOF) | Rejected |
| Arbitrary data whose first declared block happens to fit | Probe may still accept at *this* requirement's floor; the residual is then narrowed by *A content probe SHALL follow a format's self-describing block chain* below |
| OLE/CFB file (`D0 CF 11 E0 A1 B1 1A E1`, ≥ 7425 bytes) | Brotli first-block gate / `BrotliCodec.content_probe` still accept (MLEN 7422 always fits). End-to-end `detect_format` today claims **LZMA Alone at `PROBABLE`** (Alone wins probe order) — not a Brotli residual at the detection layer |
| COFF-shaped prefix (`64 86 …` with a fitting uncompressed trailer) | Same split: Brotli gate accepts; end-to-end Alone at `PROBABLE` |
| A 13-byte text file, LZMA Alone probe | **Rejected** — a source that is only the 13-byte header cannot be an Alone stream (removes the entire measured real-world Alone residual, 4 of 4) |
| Non-seekable source of unknown length (≥ `DETECTION_LIMIT` peek) | Gate skipped; today's behaviour |
| Non-seekable source shorter than the detection peek | Length inferred from the short peek; gate applies |
| Source length known to be shorter than the declared metadata skip | Rejected |

## ADDED Requirements

### Requirement: A content probe SHALL NOT accept an incomplete stream it can see whole

A content probe decodes a bounded prefix and treats "the decoder wants more input" as a
match, because a real stream usually continues past the prefix. When the probe knows the
source length and that length does not exceed the bytes it was handed, there is no more
input: the probe is holding the entire file. A **complete valid stream that finishes within
a declared output drain terminates**, so a decode that still wants more input after that
bounded drain SHALL be a rejection rather than a match.

This is a **bounded** completeness check, not a full drain of the decoded output: the
implementation SHALL declare an output budget (today: 64 KiB) so a highly-compressible
fully-visible sample cannot expand into an unbounded decode. Streams whose expansion
exceeds that budget without the decoder signalling "needs more input" are not rejected
here. The check MUST NOT be implemented as a minimum source size. A 9-byte
`brotli.compress(b"hello")` finishes within the drain and SHALL still be accepted.

The rule SHALL apply to every probe that decodes, not to Brotli alone: it follows from
bounded decoding rather than from any one format's framing. Measured on 66 361 real files
(at the then-256-byte completeness drain; the drain is now 64 KiB, which only strengthens
the rule), it rejects **91 of 128** fabricated probe claims (71%) — 67 of them under
16 bytes — while costing **zero** genuine streams.

The check SHALL be skipped when the source length is unknown, exactly as the framing gate
is; detection then behaves as before.

#### Scenario: completeness matrix

| Case | Expected |
| --- | --- |
| 9-byte real Brotli stream, whole file in the prefix, decodes to completion | Accepted |
| 5-byte text file whose first meta-block parses as compressed, decode wants more input within the output drain | **Rejected** — the file is fully visible and does not terminate |
| Truncated high-ratio zlib (fully visible; expands past the old 256-byte probe read, within ~64 KiB) | **Rejected** within the declared output drain |
| Truncated high-ratio zlib whose expansion exceeds the output drain before signalling truncation | Not rejected here — bounded check cannot disprove |
| Real Brotli file larger than the prefix, decode wants more input | Accepted — there genuinely is more input |
| Real Brotli file exactly the size of the prefix, decodes to completion | Accepted |
| Source length unknown (non-seekable stream longer than the peek) | Rule skipped; today's behaviour |
| LZMA Alone: 51-byte low-entropy file, fully visible, does not terminate | **Rejected** — same rule, not a Brotli special case |
| Any source larger than the prefix | Rule does not apply; other rules decide |

### Requirement: A content probe SHALL follow a format's self-describing block chain

**Scope: formats whose blocks are byte-aligned and self-describing, so a successor's offset
is known without decompressing. Brotli is the only such format today.** For those, a probe
SHALL follow the chain to test the same framing invariant beyond the first block, and SHALL
reject a link that overruns the source or a declared end that leaves trailing bytes. A
format outside that scope is unaffected — this is not an obligation on every probe.

The walk is mandatory rather than optional because the alternative is a probe whose
false-positive rate silently depends on whether an implementer felt like walking. What is
*bounded* is the work, not the obligation: the budgets below are the escape hatch, and
exhausting either is a defined outcome rather than a licence to skip the walk.

The walk exists because the first-block check goes **vacuous on large sources**: Brotli's
MLEN field tops out at 2²⁴, so past ~16 MiB every declared length fits trivially. Measured
on random blobs, the walk takes 16 MiB acceptance from 8.33% (where the first-block check
buys nothing) to 2.00%, and a `/usr` tree from 61 survivors to 14.

The walk SHALL be **bounded by a declared link count** (today: 8) and, on forward-only
sources, by a **declared maximum absolute offset** for probe reads (today: 1 MiB). Reaching
either means *cannot disprove*: the probe SHALL keep the verdict the earlier rules reached
and MUST NOT reject on that basis. This is the same discipline as an unknown source length
— absence of evidence is not evidence against, so budget exhaustion can never manufacture a
false negative. The 1 MiB figure is the memory-governing ceiling for a non-seekable
`read_at` (buffering `[0, offset)`); seekable sources and paths may seek past it.

The walk stops at the first compressed block, which carries no declared length to check.
On a real Brotli file whose first meta-block is compressed — 79 of 150 in the corpus — it
therefore terminates immediately, having read four bytes.

Following the chain requires bytes at offsets that may lie past the peeked prefix. The
mechanism by which a probe reaches them is settled in this change's design; whatever it
is, the reads SHALL stay within the declared bounds and SHALL NOT decompress.

#### Scenario: chain walk matrix

| Case | Expected |
| --- | --- |
| Real `.br` file, first meta-block compressed | Walk stops at once; accepted |
| Real `.br` file, uncompressed first block, all links fit | Accepted — every declared length is honoured |
| Fabrication whose first block fits but whose second link overruns the source | **Rejected** |
| Fabrication whose chain reaches a declared end with bytes left over | **Rejected** |
| 16 MiB source whose first declared block fits trivially (MLEN ceiling) | Walk decides; first-block check alone would have accepted |
| Chain longer than the link bound | Verdict unchanged from the earlier rules; **not** a rejection |
| Non-seekable `read_at` past the 1 MiB offset ceiling | Declined → cannot disprove; earlier verdict stands |
| OLE/CFB file ≥ 7425 bytes | Still accepted — its constant magic yields a fitting chain. Known residual, unchanged |

