# Brotli content probe — investigation results

**Brief:** `dev-docs/investigations/brotli-content-probe-brief.md` (lands with PR #254)
**Registered as:** `dev-docs/open-issues.md` P12, `dev-docs/threat-model.md` O10 (both from #254)
**Date:** 2026-08-19
**Status:** complete. No production code changed — the brief asks for findings plus, if
warranted, an OpenSpec proposal. A proposal is warranted; §7 says what it should contain.

**Environment.** `main` @ `d43375f`, CPython 3.11.15, `Brotli` 1.2.0 (the C extension),
glibc 2.39, Linux 6.18.5. Measurement scripts are reproduced inline in §8; every number
below was produced by them in this environment.

---

## TL;DR

The ~8% is **not** decoder looseness or a tuning failure. It is one specific,
fully-explained feature of RFC 7932: **a non-last *uncompressed* meta-block**. Its header
is four bytes with only ~15 constrained bits, and everything after it is literal output.
Roughly 6.4% of random data parses as one, and the decoder then happily copies bytes until
the bounded prefix runs out. A metadata meta-block (declared skip length, no output) adds
most of the rest. Analytic prediction **8.10%**, measured **8.16%** — the model is closed.

Three consequences, each measured:

1. **Probe tuning cannot work, and now there is a reason why.** Both accepting paths
   declare a length and emit no evidence; a bigger prefix just gets more literal bytes
   copied, and demanding output is the same test the uncompressed path already passes.
2. **The information the probe is missing is the file size.** Both paths declare a byte
   count that a complete valid file *must* physically contain. Checking the declaration
   against the file cuts random-data false positives **25×** (4 KiB files) with **zero**
   false negatives on 150 real streams — 56 of which take the uncompressed path themselves.
   Following the declared chain across meta-blocks does better still, and is the only
   variant that helps files ≥ 16 MiB.
3. **The impact is narrower than P12/O10 record.** Across 364 false positives at three file
   sizes, **not one** produced a silent wrong answer: listing succeeds, but every read
   failed (`TruncatedError` 97.5%, `CorruptionError` 2.5%). The defect is a *misleading
   listing and a misattributed error*, not silent data fabrication. The genuinely silent
   case needs crafted input — and crafted input really is valid Brotli, so no probe can
   refuse it (§3).

Also settled: **`MZ` is a legal Brotli stream prefix** — §3 builds one and round-trips it
through the reference decoder. But **`MZ` followed by a *valid DOS header* is not**, and
provably so (§3.1): a valid `e_cblp` is arithmetically disjoint from what Brotli's MLEN
encoding requires at those byte positions. Real PE files are already rejected — **0/60**
across four independent toolchains — and the `MZ` + `\x90`×4094 fixture is accepted only
because its filler is *not* what a DOS header looks like. That explains why #254's cue split
is safe, but it does **not** justify loosening the STRONG test to `e_cblp`: §7 measures
today's `e_lfanew` → `PE\0\0` rule at ~2⁻⁵² against `e_cblp`'s 2⁻⁷. **`\x7fELF` is likewise
impossible**, deterministically — one of **54 first bytes that can never begin a valid
Brotli stream**. And the peer probes are clean: zlib and LZMA Alone take **0/20 000**
random blobs each, so this is Brotli's problem alone.

---

## 1. Why `MZ` + `\x90`×4094 decodes (brief Q1)

Walk `4D 5A 90 90` as RFC 7932 reads it — LSB-first within each byte (§1.5):

| bits | field | value |
| --- | --- | --- |
| byte 0 bit 0 | "WBITS > 16" | 1 |
| byte 0 bits 1–3 | `n` | 6 → **WBITS = 17 + 6 = 23** |
| byte 0 bit 4 | `ISLAST` | **0** |
| byte 0 bits 5–6 | `MNIBBLES` code | 2 → **6 nibbles**, so MLEN is 24 bits |
| byte 0 bit 7 … byte 3 bit 6 | `MLEN` − 1 | 2 171 060 → **MLEN = 2 171 061** |
| byte 3 bit 7 | `ISUNCOMPRESSED` | **1** |

That lands exactly on a byte boundary at byte 4, so the zero-padding rule is vacuous. The
meta-block is a **non-last uncompressed block of 2 171 061 bytes**, and the decoder's job
from there is `memcpy`. Given a 256-byte prefix it emits `256 − 4 = 252` bytes and stops
because the input ran out — which is precisely the observed 252, and why it is not a
truncation *error*: the decoder is mid-block and content with that.

The contrast pair in the brief isolates two different rules, not one:

| prefix | what changes | verdict |
| --- | --- | --- |
| `MZ` + `\x90`… | as above | **accepted** — uncompressed meta-block |
| `MZ` + `\x00`… | MLEN's top nibble becomes 0 | rejected — with `MNIBBLES > 4` the most significant nibble must be non-zero (`EXUBERANT_NIBBLE`) |
| `MZ` + `A`… | MLEN is fine, but byte 3 bit 7 is 0 | rejected — `ISUNCOMPRESSED = 0`, so it must parse Huffman tables, and does not |

So `\x90` is not incidental filler. `0x90` supplies both the non-zero top MLEN nibble and
the set `ISUNCOMPRESSED` bit; `0x00` fails the first test and `0x41` fails the second.

For the whole 8%, the same reasoning generalises into a header parser
(§8, `hdr.py`) that classifies a prefix without decoding it. Over 200 000 random
300-byte blobs, every class it calls invalid is accepted **0%** of the time by the real
decoder, and acceptance concentrates in exactly two classes:

| first meta-block parses as | share of blobs | of those, probe accepts | contribution to the 8.16% |
| --- | --- | --- | --- |
| **uncompressed** | 6.46% | 99.84% | **6.45%** (79% of hits) |
| **metadata** (declared skip) | 3.31% | 51.35% | **1.70%** (21% of hits) |
| compressed (Huffman body) | 35.62% | 0.02% | 0.006% |
| empty-last (`ISLAST`+`ISLASTEMPTY`) | 3.15% | **0.00%** | — |
| structurally invalid (5 rules) | 51.46% | 0.00% | — |

Two details worth keeping:

- **The metadata path only half-works**, because after the declared skip the decoder parses
  the *next* meta-block header and usually fails there. That is the same chain the gate in
  §4 walks deliberately.
- **Empty-last is valid Brotli that never passes.** `b"\x06"` alone decodes to `b""`, but
  `b"\x06" + anything` is rejected: **the decoder refuses trailing bytes after a stream
  ends.** So the decoder already enforces whole-file consistency at the *end* of a stream —
  it simply never reaches the end, because the accepting paths declare lengths far beyond
  the prefix. That asymmetry is the whole bug, and §4 is its fix.

## 2. The rate, derived (brief Q2)

The header is a small deterministic tree, so the acceptance probability on uniform random
input can be computed exactly rather than sampled (§8, `m5_analytic.py`):

| first meta-block parses as | analytic | measured (200k blobs) |
| --- | --- | --- |
| uncompressed | 6.4187% | 6.46% |
| metadata | 3.2895% | 3.31% |
| empty-last | 3.1357% | 3.15% |
| compressed | 35.6567% | 35.62% |
| reserved WBITS | 0.7812% | 0.74% |
| **predicted probe acceptance** | **8.10%** | **8.16%** |

**How many bits are actually constrained?** For the dominant path: 1–7 bits of WBITS (of
which exactly one 7-bit pattern is reserved — 1/128), `ISLAST` = 0, a 2-bit `MNIBBLES`,
a 4-bit non-zero top nibble when `MNIBBLES > 4`, `ISUNCOMPRESSED` = 1, and however many
padding bits reach the byte boundary. That is **about 15 constrained bits out of the
32 consumed** — and 17 of those 32 are MLEN payload that constrains nothing. `2⁻⁴` from
the two flag bits and the code, times the nibble and padding factors, is the 6.4%.

**Is the rate an artifact of short accidental streams?** No — it is the opposite. The
brief wondered whether demanding decoded output helps because accidental streams are
short. They are not short; they are *unbounded*. The uncompressed path emits `prefix − 4`
bytes, so it satisfies "produced output" trivially and satisfies "≥ 512 bytes" as soon as
the prefix is ≥ 516. That is exactly why the brief's own table shows a 4096-byte prefix
with a ≥512-byte demand still sitting at 8.13%: the bigger prefix hands the same path
more bytes to copy. Requiring output measures the wrong thing — every FP here is a
*declaration*, and declarations cost the forger nothing.

## 3. Can a legal Brotli stream begin with `MZ`? (brief Q3)

**Yes — constructively.** `MZ\x90\x90` is a complete, well-formed header for a non-last
uncompressed meta-block of 2 171 061 bytes. Append that many literal bytes and a
one-byte empty-last meta-block (`\x03`) and you have a 2 171 066-byte file that the
reference decoder accepts and that round-trips exactly:

```
stream = b"MZ\x90\x90" + payload + b"\x03"        # payload = 2_171_061 bytes
brotli.decompress(stream) == payload              # True
```

So the SFX change's decision to leave a bare `MZ` prefix able to reach the content probes
is correct, and **cannot** be tightened to a strong cue on legality grounds. `MZ` proves
nothing in either direction.

The weaker "would a real encoder ever emit it?" question is more constrained but does not
rescue the cue. `M` = `0x4D` forces WBITS = 23 (low nibble `0xD`) *and* `ISLAST` = 0 *and*
`MNIBBLES` = 6, i.e. an `lgwin=23` stream whose first meta-block is 1–16 MiB; `Z` then
pins MLEN ≡ 181 (mod 512). The reference encoder does emit `0xD` low nibbles at
`lgwin=23` (it is deterministic in `lgwin`: `0x1`/`0x3` at 10, `0xB` at 22, `0xD` at 23,
`0xF` at 24), but across the multi-meta-block inputs tried here it chose `ISLAST=1` or
5-nibble lengths, so no encoder-produced `MZ` turned up. That is a statement about
encoder heuristics, not about legality — and detection must accept legal streams, not
merely fashionable ones.

**`\x7fELF` is a different story.** `0x7F` forces WBITS = 24, then `ISLAST` = 1,
`ISLASTEMPTY` = 1 — the stream ends inside byte 0 — and the remaining bits must be zero
padding. Bit 6 of `0x7F` is 1, so it is **deterministically invalid, for every possible
continuation**. That is the mechanism behind the brief's 0/887 ELF binaries: not a
statistical near-miss, a structural impossibility.

Generalising that: exactly **54 of 256 first bytes can never begin a valid Brotli
stream** — 52 by the empty-last padding rule and 2 (`0x11`, `0x91`) by the reserved WBITS
pattern. Verified over 216 000 trials with random tails: zero acceptances.

```
0e 11 16 1e 26 2e 36 3e 46 4e 56 5e 66 6e 73 75 76 77 79 7b 7d 7e 7f 86 8e 91 96 9e a6 ae
b3 b5 b6 b7 b9 bb bd be bf c6 ce d6 de e6 ee f3 f5 f6 f7 f9 fb fd fe ff
```

Note `0x7f` (ELF) and `0xff` are both in it; `0x4d` (`M`) and `0x23` (`#!`) are not.

### 3.1 Where the `\x90` filler came from, and why `e_cblp` closes the case

`MZ` + `\x90`×4094 is `_STUB` in `tests/test_sfx.py` (#254), and its own comment calls it
"deliberately the *synthetic* one". `\x90` is x86 `NOP` — filler chosen to look like code,
not copied from a real executable. It matters more than a fixture choice usually would,
because **`\x90` is exactly what makes it decode**: §1 showed byte 3 supplies both the
non-zero MLEN top nibble and the set `ISUNCOMPRESSED` bit.

A real PE does not look like that. Every PE DOS header begins `4D 5A 90 00` — byte 2 is
`0x90` (that is `e_cblp` = 144, bytes-on-last-page), but **byte 3 is `0x00`**. The header
shape alone is enough: `MZ\x90\x00…` fails on `EXUBERANT_NIBBLE`.

That is not one linker's habit. Across **60 distinct PE binaries from four independent
toolchains** — MSVC (distlib launchers, plus `.pyd`/`.dll` from the numpy, Pillow, lxml,
cffi, cryptography, msgpack and PyYAML Windows wheels), **Go's own internal linker**, and
**MinGW-w64's GNU `ld`** — bytes 2–3 are `90 00` in **60/60**, `e_cp` is 3 in 60/60, the
`e_lfanew` signature is `PE\0\0` in 60/60, and the Brotli probe claims **0/60**. Every
toolchain embeds the same canonical "This program cannot be run in DOS mode" stub, so the
prologue is effectively universal in practice rather than vendor-specific.

That generalises into a provable rule, which is the useful part:

> For a prefix starting `MZ`, byte 0 = `0x4D` pins `MNIBBLES` to code 2 (six nibbles), so
> MLEN's most significant nibble is **byte 3 bits 3–6** and must be non-zero. Hence a legal
> Brotli stream beginning `MZ` requires **byte 3 ≥ 0x08**, i.e. **`e_cblp` ≥ 2048**.
> `e_cblp ≤ 512` forces byte 3 ≤ 2. **The two conditions cannot both hold.**

Verified exhaustively over all 65 536 `(byte2, byte3)` pairs: **0 overlap**. So validating
`e_cblp` in the SFX executable cue costs *nothing* in Brotli detection — it cannot reject a
stream the probe could legitimately want, because no such stream exists. And it is a sharp
discriminator: only 513/65 536 = 0.78% of random 2-byte values satisfy `e_cblp ≤ 512`
(measured 0.90% on 20 000 random `MZ`-prefixed blobs).

This also puts a number on how exposed the SFX path specifically is. Random data *forced*
to start with `MZ` is accepted by the Brotli probe **47.19%** of the time — not 8% — because
`M` pins WBITS = 23, `ISLAST` = 0 and `MNIBBLES` = 6 all at once, which is a far more
favourable configuration than average. The `MZ` prefix makes the collision six times more
likely, and `e_cblp` removes it entirely.

The residual honesty: the Windows loader ignores every DOS header field except `e_lfanew`
and the `MZ` magic, so a hand-built PE *may* carry a nonsense `e_cblp` and still run. The
rule above is sound in the direction that matters (valid `e_cblp` ⇒ not Brotli); it is not a
guarantee that every loadable PE has one. Real linker output does, which is what an SFX stub
is.

## 4. Is there a cheap structural pre-gate? (brief Q4)

**Yes, and it is a good one.** Not a first-byte table — that is free but only worth 21% —
but the observation that both accepting paths *declare a byte count*:

> An uncompressed meta-block declares MLEN raw bytes that must physically follow it. A
> metadata meta-block declares MSKIPLEN bytes likewise. For a **complete** file,
> `header_bytes + declared_length ≤ file_size` is therefore an invariant, not a heuristic.

Random data has no such obligation, and the numbers follow:

| file size | probe today | + first-block framing check | + full chain walk |
| --- | --- | --- | --- |
| 4 KiB | 8.09% | **0.325%** (25×) | **0.050%** (162×) |
| 64 KiB | 8.38% | 4.16% (2×) | **1.55%** (5.4×) |
| 1 MiB | 6.75% | 3.75% (2×) | **1.45%** (4.7×) |
| 16 MiB | 8.33% | 8.33% (1×) | **2.00%** (4.2×) |

**False negatives on 150 real Brotli streams** (payloads from empty to 1 MiB, qualities
0/1/5/9/11, `lgwin` 10/22/24): **0 for both variants.** That is not luck — 56 of those 150
genuinely start with an uncompressed meta-block (every `one-byte`, `text-small`,
`random-4k` and `random-1m` case), and every one satisfies the invariant by construction.
The check is sound by definition, and the corpus confirms the definition was read right.

The **chain walk** is the stronger form. Uncompressed and metadata meta-blocks are
byte-aligned and self-describing, so the *next* header's offset is known exactly without
decompressing anything: parse, jump, parse again, stop at the first compressed meta-block
(whose length is only knowable by Huffman-decoding it) or at the declared end. Cost is one
~24-byte read per link, capped; for a real Brotli file whose first meta-block is compressed
— 79 of the 150 — the walk stops immediately, having read four bytes. It is also the only
variant that does anything for files ≥ 16 MiB, where MLEN's 2²⁴ ceiling makes the
single-block check vacuous.

Both need the file size, which the probe signature `content_probe(prefix: bytes) -> bool`
does not carry. Detection can supply it: `os.path.getsize` for a `Path`, `seek(0, 2)`
relative to the start position for a seekable stream, and for anything else the peek itself
— `_peek_prefix` returns short when the file is shorter than the request, so any file below
`DETECTION_LIMIT` (4096) already reveals its exact size. Only a non-seekable stream longer
than the peek has no size, and there the gate degrades to today's behaviour.

For completeness, the tuning variants re-measured on one shared corpus (20 000 random 4 KiB
blobs, 1200 system binaries, 150 real streams):

| policy | random FP | binary FP | real misses |
| --- | --- | --- | --- |
| 256B prefix, any outcome (today) | 8.19% | 0/1200 | 0/150 |
| 256B prefix, ≥1 byte out | 6.50% | 0/1200 | 24/150 |
| 256B prefix, ≥512 bytes out | 0.00% | 0/1200 | 90/150 |
| 4096B prefix, any outcome | 7.94% | 0/1200 | 0/150 |
| 4096B prefix, ≥512 bytes out | 6.33% | 0/1200 | 45/150 |
| **256B prefix + framing gate** | **0.33%** | 0/1200 | **0/150** |
| **4096B prefix + framing gate** | **0.07%** | 0/1200 | **0/150** |

(The ≥512-byte rows differ from the brief's, which counted a truncation as a hit regardless
of how much came out; here the byte count is required outright. Both agree on the
conclusion, and on the direction of every trade.)

### 4.1 A gate that looks attractive and is not: restricting WBITS

WBITS is the obvious candidate, because its encoding is lopsided. The 15 legal values are
*not* equiprobable in random data — WBITS = 16 is a **one-bit** encoding, so it takes half
of all random prefixes, while 18–24 take four bits each:

| WBITS | encoding | share of random data |
| --- | --- | --- |
| 16 | 1 bit | **50.0%** |
| 18–24 | 4 bits | ~6.2% each |
| 10–15, 17 | 7 bits | ~0.8% each |
| (reserved) | 7 bits | 0.78% |

So a whitelist bites hard. Restricting the probe to WBITS = 22 — the reference encoder's
default — takes random-data FP from **8.30% to 0.79%**, a 10.5× cut.

**But the encoder emits whatever it is asked for, and real files use the full range.**
Measured across quality × `lgwin` × payload: at quality ≥ 2 the encoder emits *exactly* the
requested `lgwin`, all 15 values 10–24 reachable; quality 0 and 1 clamp anything below 18 up
to 18. Nothing narrows it. And the wild confirms it — every real Brotli stream I could find
on this machine, verified by decompressing it:

| source | WBITS |
| --- | --- |
| `/usr/share/javascript/underscore/underscore.min.js.br` | **15** |
| `…/underscore.min.js.map.br` | **16** |
| 4 WOFF2 fonts (Font Awesome) | **19** |
| 8 WOFF2 fonts (Lato, Roboto Slab, Fraunces) | **22** |

The two `.br` files — the exact format the brief insists must keep working — use 15 and 16.
The `brotli` CLI shrinks the window toward the input size, so small-window streams are the
*normal* case for small files; only the Python binding's `lgwin=22` default keeps 22 common.
Whitelisting even the union observed here, {15, 16, 19, 22}, is worth just **2.1×** (8.30% →
4.00%) because it has to admit 16 — the one value that is half of all random data.

| whitelist | random FP | reduction | rejects |
| --- | --- | --- | --- |
| {22} only | 0.79% | 10.5× | both real `.br` files, 4/12 fonts |
| {15, 16, 19, 22} (observed) | 4.00% | 2.1× | nothing observed — but still a guess |
| {16…24} | 8.15% | 1.0× | the `.br` file at WBITS 15 |

So WBITS whitelisting is the wrong shape of lever: the variants that pay are unsound
(they reject files archivey claims to support), and the variant that is safe barely pays.
The framing check in §4 gets 25–162× while being *sound* — it cannot reject a valid complete
file, because the property it tests is one such a file must have. Not recommended.

## 5. What actually happens on a false positive

Worth pinning down before choosing a policy, because it changes what "silent wrong answer"
means here. Across 364 false positives at 4 KiB, 64 KiB and 256 KiB:

| stage | outcome |
| --- | --- |
| `detect_format` | `ArchiveFormat.BROTLI`, confidence `PROBABLE`, `detected_by="content_probe"` |
| `open_archive(...).members()` | succeeds — **one** fabricated member |
| reading that member | **fails every time**: `TruncatedError` 97.5%, `CorruptionError` 2.5% |

Zero silent successes. That follows from §1: the accepting paths declare lengths the file
cannot honour, so the read runs off the end. It also means the user-visible defect is
sharper than "fabricated data" — it is **the wrong error**. A file that should have raised
`FormatDetectionError` ("unrecognized format") instead raises `TruncatedError (…,
format=BROTLI)`, which blames the file for being truncated and names a format it never was.

Two other pieces of the realistic picture:

- **Structured files are not at risk.** 0/950 real repo files (source, Markdown, JSON,
  fixtures), 0/1200 system binaries, and the brief's 0/887 ELF binaries. The accepting bit
  patterns need entropy that text and executables do not have — and for ELF, §3 shows it is
  outright impossible.
- **High-entropy *bodies* are.** Reading real compressed/media files from an offset (so the
  container header is gone, i.e. a headerless blob) gives 1.5–2.6% acceptance, and
  cryptographic random gives 8.05% — indistinguishable from uniform random, and cut to
  0.40% by the framing gate. So the exposed case is exactly "an opaque high-entropy blob
  with no magic", which is also the case where a clean `FormatDetectionError` is the most
  useful answer.

The residual after gating is real but small and bounded: at 4 KiB, 0.325% survive (40
uncompressed, 24 metadata, 1 compressed per 20 000), all with declared lengths that
genuinely fit inside the file.

## 6. Do the peer probes share the problem? (brief Q6)

No. Measured identically — 20 000 random 4 KiB blobs and 1200 system binaries:

| codec | random 4 KiB FP | system-binary FP |
| --- | --- | --- |
| `lzma_alone` | **0.000%** | 0/1200 |
| `zlib` | **0.000%** | 0/1200 |
| `brotli` | 8.190% | 0/1200 |

The two are clean for different reasons, and only one of them is the header gate:

- **zlib** is gated by construction — `_ZLIB_HEADERS` admits 4 of 65 536 two-byte prefixes
  (0.006%) before any decode, and the decode then has to succeed too.
- **LZMA Alone**'s gate is weak: every properties byte ≤ 224 is a valid `(lc, lp, pb)`
  triple, so ~88% of random data passes `_alone_header_plausible` and reaches the decoder.
  It scores 0 anyway because the LZMA decoder is *intrinsically* strict — there is no
  "copy the rest verbatim" escape hatch in its bitstream. Brotli's uncompressed meta-block
  is that escape hatch.

So the fix belongs in Brotli, not at the probe layer generally. The transferable lesson is
narrower: a probe is only as strong as the weakest thing the format lets a stream *declare*
without proving.

## 7. What detection should do (brief Q5)

The brief listed four options. With the measurements in, three of them are worse than the
option the measurements produced.

**Recommended — add the structural gate; leave the probe itself alone.** Gate the Brotli
probe on the framing invariant of §4, chain-walk form, using the file size where detection
can get it and falling back to today's behaviour where it cannot. It is sound rather than
heuristic (a complete valid file cannot fail it), it costs a bounded number of small reads
and no decompression, it is 4×–162× depending on file size, and it measured **0/150** false
negatives on a corpus deliberately stocked with the streams most likely to trip it. Bare
`.br` files keep working — that is what the 0/150 means, and it is the constraint the brief
puts above the others.

On the brief's four:

| option | verdict |
| --- | --- |
| require `.br` extension corroboration; report `GUESS` otherwise | **Rejected.** It breaks detection of correctly-formed extensionless `.br` streams — the format's whole problem is that content is all there is. The gate gets a bigger win without giving that up. |
| demand more of the decoded bytes (reach a meta-block boundary) | **Rejected as primary.** §2: the uncompressed path *is* a meta-block in progress and emits as much as you ask for. Every output-demanding variant trades FP for FN roughly one-for-one (24–90 misses of 150). |
| fail loudly when a single-file result rests only on a content probe | **Worth doing, separately, and narrowly.** Not as a refusal — that would break `.br` — but the `PROBABLE`/`content_probe` provenance is already on `FormatInfo` and is currently discarded downstream. §5 shows the wrong-error problem is the real user-visible harm; a read failure on a probe-only single-file result should say "this may not be a Brotli file" rather than "file is truncated". |
| accept the rate and document it | **Superseded.** It was the right call while probe tuning looked like the only lever. It is not the only lever. |

**Separately, for the SFX cue (#254).** An earlier draft of this section proposed promoting
the `MZ` cue to STRONG on a valid `e_cblp`, *independently* of the `e_lfanew` → `PE\0\0`
follow-through. **That was wrong, and measuring the alternatives is what shows it.** Over
200 000 random `MZ`-prefixed blobs:

| candidate STRONG rule | real PEs | random `MZ` FP | analytic bound |
| --- | --- | --- | --- |
| `e_cblp ≤ 512` | 60/60 | **0.7695%** | 513/2¹⁶ = 0.783% |
| bytes 2–3 == `90 00` | 60/60 | 0.0025% | 1/2¹⁶ = 0.0015% |
| `e_lfanew` → `PE\0\0` (**today's rule**) | 60/60 | **0.0000%** | ~2⁻⁵² |
| `e_lfanew` → `PE`/`NE`/`LE`/`LX` | 60/60 | 0.0000% | ~2⁻⁵⁰ |

The existing rule already wins by a margin nothing else approaches: `e_lfanew` is four
random bytes that must land inside the prefix (~2⁻²²) *and* address four more specific bytes
(2⁻³²). Swapping in `e_cblp` would have made the cue roughly **10¹³ times looser**, and even
the tightened `90 00` form is 2⁻¹⁶ against 2⁻⁵². So:

- **Keep `e_lfanew` → `PE\0\0` as the STRONG rule.** Do not add `e_cblp` as an alternative
  path to STRONG. Retracted.
- **Optionally widen the accepted signature to `NE`/`LE`/`LX`** alongside `PE`. Still
  0.0000% measured, still ~2⁻⁵⁰ analytically, and it covers 16-bit Windows and OS/2 SFX
  stubs that the `PE`-only test rejects. Caveat: no such stub was available to test here —
  every one of the 53 `MZ` files on this machine is a `PE` — so this is reasoning from the
  format, not from a corpus.
- **`e_cblp`'s real value is explanatory, not operational.** It is what *proves* real
  executables can never collide with the Brotli probe, which is precisely the assurance
  #254's weak/strong split was asserting from outcomes. As a runtime check it would only be
  a micro-optimisation (two bytes, no `e_lfanew` read) and is not worth the code.
- **Narrowing STRONG to bytes 2–3 == `90 00`** is tighter than `e_cblp ≤ 512` and holds
  60/60 here, but it is a toolchain convention, not a format requirement: a genuine DOS-era
  `MZ` binary carries `e_cblp` = filesize mod 512, i.e. effectively arbitrary. Since the
  `e_lfanew` test is both tighter *and* grounded in the PE spec, `90 00` has no role.
- The `MZ` + `\x90`×4094 fixture should be labelled for what §3.1 shows it is: not a stub
  with unlucky filler, but a byte sequence whose filler is *specifically* what a DOS header
  never contains. It is a fine regression test; it is not evidence about real executables,
  and #254's design notes read as though it were.

**Scope note for the proposal.** Any of this moves normative text in
`openspec/specs/format-detection/spec.md` (the probe requirements) and touches the
`content_probe(prefix) -> bool` signature, since the gate needs a size the probe cannot
currently see. The brief is explicit that this must land as an OpenSpec change rather than
in passing, and that remains right. The proposal should cover: the framing invariant as a
normative requirement, the size-availability fallback (including the non-seekable case),
the `content_probe` signature change and what it means for the zlib/LZMA probes that do not
need it, and the error-message provenance item above. The residual (§5) does not close and
should stay registered — P12/O10 want rewording to "misleading listing and misattributed
read error" rather than "silent wrong answer", which §5 measured as not occurring.

**One thing that will not close, ever.** A crafted file *is* a valid Brotli stream — §3
builds a 2 MB one starting with `MZ`. Brotli has no magic, so "this file is simultaneously
valid Brotli and something else" is a property of the format, not a defect in the probe.
No gate proposed here changes that, and none should claim to.

## 8. Reproducing

Scripts are self-contained; run them against `main` with the repo venv. The header parser
they share (`hdr.py`) is ~130 lines and implements only WBITS plus the first meta-block
header — everything that reaches a Huffman body is reported as undecided and handed to the
real decoder.

```python
# The whole finding in six lines.
import brotli
from archivey.internal.streams.codecs import _BY_STREAM_FORMAT
from archivey.types import StreamFormat

codec = _BY_STREAM_FORMAT[StreamFormat.BROTLI]
assert codec.content_probe(b"MZ" + b"\x90" * 4094)          # accepted
assert len(brotli.Decompressor().process(b"MZ" + b"\x90" * 254)) == 256 - 4
# ... because MZ\x90\x90 is a 4-byte header for an uncompressed meta-block of 2_171_061
# bytes, and the decoder just copies. Give it the bytes it asked for and it is a real file:
payload = bytes((i * 7 + 11) % 256 for i in range(2_171_061))
assert brotli.decompress(b"MZ\x90\x90" + payload + b"\x03") == payload
```

| script | what it produces |
| --- | --- |
| `hdr.py` | the RFC 7932 header parser used by everything below |
| `m1_validate.py` | §1's classification table; also the check that every class the parser calls invalid is accepted 0% by the real decoder |
| `m2_overrun.py`, `m6_residual.py` | §4's framing check, and §5's residual breakdown |
| `m3_variants.py` | §4's policy table (FP/FN across all probe variants) |
| `m4_prefixes.py` | §3: the `MZ` construction, encoder reachability, the 54 impossible first bytes |
| `m5_analytic.py` | §2's analytic derivation |
| `m7_chain.py` | §4's chain walk across file sizes |
| `m9_impact.py`, `m10_realworld.py` | §5: listing-vs-reading outcomes, and the real-file negatives |
| `m11_mz.py` | §3.1: real PE headers, and the exhaustive `e_cblp` disjointness check |
| `m13_dos.py` | §3.1/§7: the 60-binary four-toolchain DOS-header survey and the STRONG-rule comparison (builds its own Go and MinGW PEs, and pulls MSVC-built `.pyd`s from Windows wheels) |
| `m12_wbits.py` | §4.1: emitted vs. requested `lgwin`, and the WBITS whitelist trade |

Scripts live in the session scratchpad, not the repo — they are measurement one-offs, and
the numbers they produced are recorded above. The two that would be worth keeping if the
proposal lands are `hdr.py` and `m3_variants.py`, as the seed of a regression test that the
gate's false-negative count stays at zero.

## Refs

- `src/archivey/internal/streams/codecs.py` — `_PROBE_PREFIX`, `_decodes_sample`,
  `BrotliCodec.content_probe`, `_ZLIB_HEADERS`, `_alone_header_plausible`
- `src/archivey/internal/detection.py` — `_peek_prefix`, `DetectionConfidence`, and where
  a probe result becomes the answer
- `src/archivey/internal/streams/peekable.py` — `DETECTION_LIMIT = 4096`
- `openspec/specs/format-detection/spec.md` — the normative probe requirements
- RFC 7932 §1.5 (bit order), §9.1 (WBITS), §9.2 (meta-block header)
