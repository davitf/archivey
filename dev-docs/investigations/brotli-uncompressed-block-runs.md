# Brotli meta-block shapes: two proposed shortcuts, both unsound

**Two questions, both from the PR #265 review discussion**, both about whether the
completeness gate's chain walk can be cheapened. §1–§5 answer the first; §6 answers a
follow-up — whether `ISLAST` is only ever set on an empty meta-block. Both answers are no.

**Question 1.** The completeness gate walks Brotli's
self-describing meta-block chain so that it can reject headers that declare an
uncompressed stream longer than the source. The chain walk is the expensive part. If a
real encoder only ever emits **one** uncompressed meta-block — "compression was not
worthwhile here, now start a compressed block" — then the walk could collapse to a
single extra header read: *the block after the first must be compressed*.

**Answer: it does not hold, and it is not close.** Runs of consecutive uncompressed
meta-blocks are the *normal* output shape for incompressible input, at every quality
setting, and the shortest counterexample this investigation produced is **13 bytes long**.
The proposed rule costs **74 false negatives on 1914 valid streams (3.9%)** — including
two files from Brotli's own reference test corpus — and buys **no measurable
false-positive reduction** over the chain walk. Keep the chain walk.

**Date:** 2026-08-25.
**Environment.** `main` @ `3dafac1`, PR #265 head `fc6bcc1`, CPython 3.11.15,
`Brotli` 1.2.0 (the C extension), reference sources `google/brotli` @ `8e10eeb` (v1.2.0),
Linux 6.18.44. Method and scripts in §8.

---

## TL;DR

| claim | verdict | evidence |
| --- | --- | --- |
| A stream can hold ≥2 consecutive uncompressed meta-blocks | **Yes, routinely** | §1, §2 |
| …only in large or exotic files | **No** — 13-byte file, three 1-byte blocks | §2.3 |
| Uncompressed block sizes have an exploitable granularity | **No** — gcd of all observed non-last lengths is **1** | §3 |
| Concatenated Brotli streams are a thing | Not for a single-stream decoder; but 1.2.0 added `brcat` / `-K` | §4.1 |
| Stream *stitching* produces such runs | **Yes** — official `BROTLI_PARAM_STREAM_OFFSET` | §4.2 |
| The encoder always terminates with an *empty* last meta-block | **No** — 92.1% end with a non-empty last compressed block | §6 |
| …so "reject ISLAST-but-not-empty" would work | **No** — it rejects 91.4% of valid streams | §6.2 |
| "Second block must be compressed" is a safe simplification | **No** — 74/1914 false negatives | §5 |
| …it at least reduces false positives more | **No** — within ±0.3 pp of the chain walk | §5.2 |

---

## 1. Why the hypothesis fails structurally

The encoder decides "compress or store" **once per meta-block, statelessly**. There are two
independent fallback paths in `c/enc/encode.c: WriteMetaBlockInternal`, and neither one
looks at what the previous meta-block did:

```c
  if (!ShouldCompress(data, mask, last_flush_pos, bytes,
                      num_literals, num_commands)) {
    ...
    BrotliStoreUncompressedMetaBlock(is_last, data, ...);
    return;
  }
  ...                                     /* try the compressed encoders */
  if (bytes + 4 < (*storage_ix >> 3)) {   /* compressed came out bigger */
    ...
    BrotliStoreUncompressedMetaBlock(is_last, data, ...);
  }
```

`ShouldCompress` is a per-block entropy test with a hard short-input rule:

```c
  if (bytes <= 2) return BROTLI_FALSE;
  if (num_commands < (bytes >> 8) + 2) {
    if ((double)num_literals > 0.99 * (double)bytes) {
      ...  /* sampled literal entropy > 7.92 bits/byte → don't compress */
```

So the question "how many uncompressed blocks in a row?" reduces to "how many consecutive
meta-blocks of this input are incompressible?" — and for an incompressible payload the
answer is *all of them*. Nothing in RFC 7932 constrains the sequence either: `ISUNCOMPRESSED`
is a free bit on every non-last meta-block.

There is also a **hard ceiling that forces the case**: `MLEN` is at most 6 nibbles, so an
uncompressed meta-block carries at most 2²⁴ = 16 777 216 bytes. Any Brotli stream over an
incompressible payload larger than 16 MiB *must* contain consecutive uncompressed
meta-blocks. In practice the bound bites far earlier, because the encoder cuts meta-blocks
at `1 << lgblock` (14, 16, 18, or `lgwin`, by quality — `c/enc/quality.h: ComputeLgBlock`).

## 2. Measured: what the reference encoder actually emits

Ground truth comes from an instrumented build of the reference **decoder** — a five-line
`fprintf` after `DecodeMetaBlockLength` in `c/dec/decode.c` — so the block sequence is read
out of the stream itself rather than inferred (§7). `U` = uncompressed, `C` = compressed,
`M` = metadata, `E` = empty-last.

### 2.1 Whole-file compression of incompressible data

`brotli -q N` over `/dev/urandom` payloads:

| input | q0 | q1 | q2 | q5 | q9 | q11 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 MiB random | `UUE` | `UUE` | `UE` | `UE` | `UE` | `UUE` |
| 1 000 003 B random | `UUE` | `UUE` | `UE` | `UUE` | `UUE` | `UUE` |
| 4 MiB random | `UUUUUUUUE` | `UUUUUUUUE` | `UE` | `UUE` | `UUE` | `UUE` |
| 5 MiB random | `UUUUUUUUUUE` | `UUUUUUUUUUE` | `UE` | `UUE` | `UUE` | `UUUE` |
| 20 MiB random | `U×40 E` | `U×40 E` | `UUE` | `U×9 E` | `U×8 E` | `U×9 E` |

A **1 MB file** is already enough at five of the six quality settings. The longest run
observed was 40.

### 2.2 Mixed content — runs interleave, they do not terminate

Concatenating random and text regions gives sequences where uncompressed runs restart:

```
random 2M + text 2M, q0    UUUUCCCCE
text 2M + random 2M, q0    CCCCUUUUE
alternating 1M × 6, q0     UUCCUUCCUUCCE
alternating 1M × 6, q1     UUCCCCCCCCUUCCCCCCCCUUCCCCCCCCE
```

`UUCCUUCCUUCCE` is the direct refutation of the "one uncompressed block, then compressed"
model: the encoder returns to uncompressed blocks whenever the data warrants, as many times
as the data warrants.

### 2.3 Streaming with flush — the case that reaches small files

This is the one that matters for detection, because it produces short streams. Under
`BROTLI_OPERATION_FLUSH` the meta-block boundaries are **the caller's chunk boundaries**,
and `ShouldCompress`'s `bytes <= 2` rule plus the Huffman-table overhead means small chunks
go out uncompressed regardless of content:

| chunks fed (3×, flush after each) | quality | file size | sequence |
| --- | --- | --- | --- |
| 1 byte of **text** | 11 | **13 B** | `UUUE` |
| 2 bytes of text | 11 | 16 B | `UUUE` |
| 4 bytes of text | 11 | 22 B | `UUUE` |
| 10 bytes random | 0/1/5/11 | 40 B | `UUUE` |
| 64 bytes random | 11 | 146 B | `UUCME` |
| 1, 2, 3, 300 bytes random | 0/1/5/11 | 319 B | `UUUUE` |
| 64 KiB random | 0/1/5/11 | 262 157 B | `UUUUE` |
| already-gzipped 523 B | 11 | 550 B | `UCMCME` |

**A 13-byte valid Brotli file with three consecutive uncompressed meta-blocks.** This is not
an exotic construction: it is `brotli.Compressor()` with `.process()`/`.flush()` per chunk,
i.e. exactly what an HTTP server doing streaming `Content-Encoding: br` does, and exactly
what any `flush()`-per-record logger does. When the payload is already compressed (images,
gzipped bodies, media), every flushed chunk lands in the uncompressed path — the
`gz_*` rows above.

Note also the `M` blocks: flushing routinely emits **empty metadata meta-blocks** as
alignment padding, so "the block after the first" is frequently metadata, not compressed.

## 3. Uncompressed block sizes: is there an exploitable granularity?

The brief hoped a non-last uncompressed block might always be a multiple of the encoder's
input-block size, which would sharpen detection. It is true *for whole-file compression*
and worthless as a rule.

Whole-file runs do show the granularity — `1 << lgblock`, per `ComputeLgBlock`:

```
q0/q1  (lgblock = lgwin)     524288, 524288, …            (2^19)
q5     (lgblock = 16)        2359296 = 36 × 65536
q9     (lgblock = 18)        2359296, 2883584, 4194304, 2621440  (all × 262144)
```

But `lgblock` is **not in the stream** (it is not `lgwin`), the final block of a run is a
remainder, and flush destroys the property outright — a flushed block is exactly the
caller's chunk length. Over the whole valid corpus:

| statistic | value |
| --- | --- |
| distinct non-last uncompressed block lengths | **74** |
| smallest / largest | **1** / **16 777 216** |
| **gcd of all of them** | **1** |
| examples from the reference test corpus | 43, 45, 16383, 16385, 99998, 213571, 378561 |

Non-last **metadata** skip lengths seen: 0, 1, 4, 7, 16. There is no floor, no ceiling below
the format's own, and no common factor. No size-shape rule is available here.

## 4. The other routes to a declaring block at position 2

### 4.1 Concatenation

The user's intuition is right that plain concatenation is not a supported single-stream
construct: `ISLAST` ends the stream and the reference decoder refuses trailing bytes.
Verified — `cat a.br a.br > c.br` gives `corrupt input` from the CLI and
`brotli: decoder failed` from the Python binding.

**But Brotli 1.2.0 (2025-10-27) added first-class support for reading them:** the CLI gained
a `brcat` alias and `-K` / `--concatenated`, and `./brotlicli -d -K` decodes the file above
successfully. So concatenated `.br` files now have a sanctioned producer story even though
`brotli.decompress()` rejects them.

*Consequence for PR #265, worth a line in the docs rather than a change:* the chain walk
rejects a concatenated file when it reaches an `ISLAST` block that does not land on EOF
(measured: `chain_proves_invalid` returns `True` for a concatenated 2 MB pair). That is
consistent with archivey's read path — the Python binding cannot read those files either —
but it turns "unreadable Brotli" into "unrecognized format". The proposed simpler rule does
not help here; it happened to *accept* the small concatenated case in testing, for the
unrelated reason that the walk stopped at a compressed block first.

### 4.2 Stream stitching — `BROTLI_PARAM_STREAM_OFFSET`

This one *is* an officially supported way to build one stream from several encoders:

> *Number of bytes of input stream already processed by a different instance. […] If offset
> is not 0, then stream header is omitted. In any case output start is byte aligned, so for
> proper streams stitching "predecessor" stream must be flushed.* — `c/include/brotli/encode.h`

Three encoder instances over 100 KB random parts each, byte-concatenated:

```
q1   300 011 B   UUUE
q5   300 017 B   UUUUUE
q11  300 017 B   UUUUUE
```

Any parallel Brotli compressor built this way over incompressible input emits consecutive
uncompressed blocks by construction.

### 4.3 Injected metadata — `BROTLI_OPERATION_EMIT_METADATA`

A caller can inject a metadata meta-block of up to 16 MiB **anywhere**, including first:

```
metadata ×2, then payload      1 098 B   MMUMUE
metadata first, then payload   1 014 B   MUUE
```

And the reference test corpus ships the extreme version:
`tests/testdata/empty.compressed.17` is **65 538 bytes of nothing but empty metadata
meta-blocks** (`.18` is 196 610 bytes of the same). Both are valid, both decode to `b""`,
and both are rejected by the proposed rule.

### 4.4 Large-window Brotli — unrelated, already settled

`--large_window=30` produces a first byte of `0x11`, one of the 54 bytes the previous
investigation proved can never start an RFC 7932 stream. The Python binding rejects it too
(`brotli: decoder failed`), so detection rejecting it stays consistent with the read path.
No action; noted so it is not rediscovered.

## 5. The trade, measured

Corpus: **1914 valid complete Brotli streams**, every one verified by a full
`brotli.decompress` round-trip first:

| source | streams |
| --- | --- |
| real WOFF2 font streams (1716 from eight `@fontsource` npm packages + 1 on this image) | 1717 |
| Brotli's own reference test corpora (`tests/testdata`, `java/.../test_data.zip`) | 60 |
| the two `.br` files shipped on this image | 2 |
| synthetic: qualities 0–11, whole-file and flushed, stitched, metadata-injecting | 135 |

(The deliberately-invalid constructions — the two concatenated files and the large-window
file — are excluded by that round-trip, as they should be.)

`chain_proves_invalid` is PR #265's implementation, unmodified. The alternative is the
literal proposal: parse the first block; if it declares a length, require the block that
follows to be compressed (an empty-last landing exactly on EOF also accepted).

### 5.1 False negatives — valid streams wrongly rejected

| rule | false negatives |
| --- | --- |
| chain walk (PR #265) | **0 / 1914** |
| "second block must be compressed" | **74 / 1914 (3.9%)** |

Breakdown of the 74:

| category | count |
| --- | --- |
| CLI-compressed incompressible payloads (random ≥ 1 MB, all qualities) | 39 |
| streaming flush / injected metadata / stitching | 33 |
| **Brotli reference test corpus** (`empty.compressed.17`, `.18`) | 2 |

All 1717 WOFF2 streams are a single compressed meta-block (`C`), so fonts are indifferent
to the choice — the earlier survey's "fonts are the wild" framing does not cover this case.

### 5.2 False positives — random data wrongly accepted

20 000 random blobs per row; "probe" is `brotli.Decompressor().process(prefix)` over a
4096-byte prefix, matching the detector.

| declared source size | probe alone | + first-block gate | + "second must be compressed" | + chain walk |
| --- | --- | --- | --- | --- |
| 4 KiB | 7.78% | 0.065% | **0.000%** | **0.000%** |
| 64 KiB | 7.75% | 3.590% | 1.100% | **1.190%** |
| 1 MiB | 7.80% | 4.135% | 1.340% | **1.370%** |
| 16 MiB | 7.61% | 7.605% | **2.380%** | 2.710% |

The two rules are within **0.33 percentage points** of each other everywhere, and they trade
places by row. That is the crux: the simplification's entire value proposition was that it
would be *nearly as good* — it is, but "nearly as good" is not a reason to accept 74 false
negatives, and the chain walk is not paying for its depth in cost either (§7).

## 6. Does the encoder always end with an *empty* last meta-block?

Asked as a follow-up, because §2's sequences end in `E` so often: if `ISLAST` were only ever
set on the empty terminator, detection could reject any header that is final but not empty —
technically laxer than RFC 7932, but detection-only.

**It does not, and the rule would reject 91% of real Brotli files.**

### 6.1 Why `E` looks universal in §2 and is not

The empty terminator is not an encoder habit; it is **forced by a format rule, and only in
one direction**. `ISUNCOMPRESSED` is read *only when `ISLAST` is clear* — see the decoder's
`BROTLI_STATE_METABLOCK_HEADER_UNCOMPRESSED` case, and the encoder saying it outright:

```c
  /* Write ISLAST bit.
     Uncompressed block cannot be the last one, so set to 0. */
  BrotliWriteBits(1, 0, storage_ix, storage);
```

So an uncompressed meta-block can *never* be last, and a stream whose final data block is
uncompressed **must** be closed with a separate empty-last block. §2 is full of `E` precisely
because §2 is about incompressible payloads. It is a biased sample of the question being
asked here.

A *compressed* meta-block has no such restriction: `ISLAST` is set on the block itself and
the stream ends there. Over the same 1914 valid streams:

| last meta-block of the stream | count | share |
| --- | --- | --- |
| **compressed, `ISLAST` set, `MLEN` > 0** | **1763** | **92.1%** |
| empty-last (`ISLAST` + `ISLASTEMPTY`) | 149 | 7.8% |
| metadata, `ISLAST` set | 2 | 0.1% |
| uncompressed | **0** | — (impossible, as above) |

And of the 149 that do end with `E`, the block before it is uncompressed in 84 cases and
metadata in 32 — the forced cases — plus 16 where a flush emitted a compressed block and the
following `finish` had nothing left to encode (`WriteMetaBlockInternal` with `bytes == 0`
writes `ISLAST`+`ISLASTEMPTY` and returns), and 17 streams that are *only* an empty-last
block.

The two metadata-terminated streams are `tests/testdata/empty.compressed.15` and `.16` from
the reference corpus, so a last metadata block is real too, not just legal.

### 6.2 What the rule would cost

The shape detection actually sees is the *first* block, and for a single-meta-block stream
the first block **is** the last one:

| first meta-block | count | share |
| --- | --- | --- |
| **compressed, `ISLAST` set** | **1748** | **91.3%** |
| uncompressed, not last | 105 | 5.5% |
| compressed, not last | 30 | 1.6% |
| empty-last | 17 | 0.9% |
| metadata, not last | 12 | 0.6% |
| metadata, `ISLAST` set | 2 | 0.1% |

Every WOFF2 font in the corpus (1717 of them) is exactly one compressed meta-block with
`ISLAST` set, and so are both `.br` files shipped on this image and most of Brotli's own
`tests/testdata/*.compressed`. Measured, against the same corpus and the same random-blob
harness as §5:

| rule | false negatives | residual FP @4 KiB / 64 KiB / 1 MiB / 16 MiB |
| --- | --- | --- |
| chain walk (PR #265) | **0 / 1914** | 0.000% / 1.205% / 1.430% / 2.695% |
| **V1** — reject a first block that is `ISLAST`-not-empty | **1750 / 1914 (91.4%)** | 7.010% / 6.935% / 7.150% / 7.190% |
| **V2** — chain walk, plus reject any *reached* block that is `ISLAST`-not-empty | **1752 / 1914 (91.5%)** | 0.000% / 0.580% / 0.780% / 1.445% |

V1 is bad on both axes at once: it rejects nine valid streams in ten and barely moves the
false-positive rate, because the probe accepts almost no blob whose first block parses as
compressed anyway (0.006 pp of the 8%, per the earlier investigation).

**V2 is the instructive one.** Its false-positive number is genuinely good — roughly half the
chain walk's residual at every size — because in random data a meta-block reached mid-chain
carries `ISLAST` about half the time, and the walk currently stops there saying "cannot
disprove". But that is the same shape as "an ordinary `.br` file is one compressed
meta-block". The rule is a sharp discriminator aimed squarely at the single most common
valid Brotli stream, which is why it scores 91.5%. A gate cannot separate them: there is
nothing else in a last compressed block's header to check, since `MLEN` there is the
*uncompressed* output length and Brotli's compression ratio has no useful upper bound.

### 6.3 What *is* sound here, and is already in place

The half of this that holds is the format rule, not the encoder habit: **an uncompressed
meta-block is never last** — confirmed over all 1914 streams, zero exceptions, and provable
from the header grammar. `brotli_framing.py` already encodes it correctly, reading
`ISUNCOMPRESSED` only when `ISLAST` is clear and never classifying a last block as
`UNCOMPRESSED`; and `chain_proves_invalid` already handles the one declaring block that *can*
be last (metadata) with `if info.is_last: return nxt != source_length`. No change available.

## 7. What the chain walk actually costs on real files

The census also answers whether the depth is doing anything. Block-sequence census over the
1914 valid streams (first 10 blocks):

```
1748  C            ← every WOFF2 font: the walk stops after one 4-byte read
  20  UE
  17  E
  17  UUE
  16  UUUE
  11  UCMCME
   8  CC
   8  UUUUE
```

**1748 of 1914 streams (91%) stop the walk at link 0** because the first meta-block is compressed —
four bytes read, no seeking. The walk only iterates on exactly the streams where the
proposed rule would have produced a false negative. `CHAIN_MAX_LINKS = 8` is never the
binding constraint on a valid file: exceeding it returns "cannot disprove", which is the
safe direction, and that is what happens on the 40-block random streams.

## 8. Method and reproduction

Everything lives under a scratch tree; the load-bearing pieces:

**Instrumented decoder.** `google/brotli` @ `8e10eeb`, one hook added to `c/dec/decode.c`
immediately after `DecodeMetaBlockLength` succeeds, inside
`case BROTLI_STATE_METABLOCK_HEADER:`:

```c
        {
          static int bd_on = -1; static int bd_idx = 0;
          if (bd_on < 0) bd_on = getenv("BROTLI_BLOCKDUMP") ? 1 : 0;
          if (bd_on) {
            size_t bd_left = BrotliGetRemainingBytes(br);
            fprintf(stderr, "BLOCK %d type=%s len=%d islast=%d unread_after_hdr=%zu\n",
                    bd_idx++,
                    s->is_metadata ? "metadata"
                                   : (s->is_uncompressed ? "uncompressed" : "compressed"),
                    s->meta_block_remaining_len, s->is_last_metablock, bd_left);
          }
        }
```

Built against a driver that feeds the whole file to `BrotliDecoderDecompressStream`:

```
gcc -O2 -o blockdump blockdump.c -I brotli-src/c/include \
    brotli-src/c/common/*.c brotli-src/c/dec/*.c -lm
gcc -O2 -o brotlicli brotli-src/c/tools/brotli.c -I brotli-src/c/include \
    brotli-src/c/common/*.c brotli-src/c/dec/*.c brotli-src/c/enc/*.c -lm
```

Note the `E` (empty-last) class is derived, not printed: `DecodeMetaBlockLength` returns
early on `ISLASTEMPTY` with all three flags clear, so a block reported as
`type=compressed len=0 islast=1` is an empty-last terminator.

**Stitching / metadata generator** (`stitch.c`): three `BrotliEncoderState`s with
`BROTLI_PARAM_STREAM_OFFSET` advanced per part and `BROTLI_OPERATION_FLUSH` between them;
and a single encoder driven with `BROTLI_OPERATION_EMIT_METADATA`.

**WOFF2 extraction:** parse `totalCompressedSize` at offset 20 and the meta/priv offsets at
28/36, then search start offsets from 48 and keep the first that round-trips through
`brotli.decompress` — 1716/1716 extracted, zero failures.

**Rule comparison** (`compare.py`, `fp.py`): imports `chain_proves_invalid`,
`parse_first_metablock` and `parse_metablock` from PR #265's
`src/archivey/internal/streams/brotli_framing.py` unmodified; the alternative rule is
implemented in ~14 lines against the same parser, so the two differ only in policy.

## 9. Recommendation

**Keep the chain walk as implemented in PR #265.** The premise it was questioned on —
that a valid Brotli stream cannot hold consecutive declaring meta-blocks — is false at
every scale from 13 bytes upward, false for the reference encoder's default behaviour on
incompressible input, false for streaming flush, false for stitching, and false for two
files in Brotli's own test corpus. Replacing the walk with a fixed second-block test would
buy nothing measurable in false positives and would misdetect roughly 4% of the valid
streams surveyed here.

**And do not add an `ISLAST`-must-be-empty rule** (§6). The empty terminator that dominates
§2's sequences is a consequence of one format rule — an uncompressed meta-block cannot be
last — not an encoder convention. 92.1% of valid streams end with a non-empty last
compressed meta-block, and for the 91.3% that are a single meta-block that is also the
*first* block, so the rule fires on the very first header of an ordinary `.br` file. The
variant that keeps the chain walk and only rejects a *reached* `ISLAST`-not-empty block
(§6.2, V2) halves the residual false-positive rate and is the tempting one; it still
misdetects 91.5% of the corpus, because the shape it keys on is what an ordinary Brotli
file looks like.

Two small follow-ups this turned up, neither blocking:

1. **Document the concatenated-stream interaction** (§4.1). `brcat` / `-K` landed in Brotli
   1.2.0; a concatenated `.br` is unreadable by archivey's backend *and* now rejected at
   detection, so the user sees "unrecognized format" rather than a Brotli error. Worth a
   line in `docs/formats.md` or the threat-model register rather than a code change.
2. **The `CHAIN_MAX_LINKS = 8` note in `brotli_framing.py`** says the real-tree census never
   needed more than 2 links. That is about *rejecting fabrications*; on valid files the cap
   is reached (40-block random streams) and returns "cannot disprove", which is correct.
   The comment could say so, so the next reader does not lower the cap thinking it is slack.
