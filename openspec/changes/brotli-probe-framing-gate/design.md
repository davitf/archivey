# Design — Brotli probe framing gate

The full investigation is `dev-docs/investigations/brotli-content-probe-results.md`
(measurements, derivations, and the rejected alternatives with their numbers). This file
records only what an implementer needs and why the shape is what it is.

## The mechanism, in four bytes

RFC 7932's meta-block header is read LSB-first. For `MZ\x90\x90`:

| bits | field | value |
| --- | --- | --- |
| byte 0 bit 0, bits 1–3 | WBITS | 23 |
| byte 0 bit 4 | `ISLAST` | 0 |
| byte 0 bits 5–6 | `MNIBBLES` code | 2 → six nibbles |
| byte 0 bit 7 … byte 3 bit 6 | `MLEN` − 1 | → MLEN = 2 171 061 |
| byte 3 bit 7 | `ISUNCOMPRESSED` | 1 |

That is a complete header for a **non-last uncompressed meta-block**, landing exactly on a
byte boundary so the zero-padding rule is vacuous. Everything after byte 4 is literal
output, so a 256-byte prefix yields 252 bytes and the decoder is content. Nothing about the
prefix size or the amount decoded distinguishes this from a real stream — the header
*declares* and the body proves nothing.

Two accepting classes, and they are the whole 8.2%: **uncompressed** (6.42% analytic /
6.46% measured) and **metadata** (3.29% / 3.31%, of which about half survive the following
header). Predicted acceptance 8.10% against 8.16% measured.

## Why the gate is sound rather than tuned

A declared meta-block asserts bytes that must physically follow it. For any **complete**
file, `header + declared <= size` therefore holds by construction — including for the 56 of
150 real corpus streams whose first meta-block *is* uncompressed (every incompressible
payload at every quality). Random data has no such obligation. That is the entire idea; it
is a consistency check between two things the file itself says, not a threshold anyone has
to choose.

Two forms, both zero-false-negative on the corpus:

| | 4 KiB | 64 KiB | 1 MiB | 16 MiB |
| --- | --- | --- | --- | --- |
| today | 8.09% | 8.38% | 6.75% | 8.33% |
| first-block check | 0.325% | 4.16% | 3.75% | 8.33% |
| chain walk | **0.050%** | **1.55%** | **1.45%** | **2.00%** |

The first-block check goes vacuous at 16 MiB because MLEN's ceiling is 2²⁴. The chain walk
is the only form that helps there, and it costs one small read per link because
uncompressed and metadata blocks are byte-aligned and self-describing — their successors'
offsets are known without decompressing anything. On a real `.br` file whose first
meta-block is compressed (79 of 150) the walk stops immediately, having read four bytes.

## Why the probe needs the source length

`content_probe(prefix: bytes) -> bool` cannot see it. Detection can: `getsize` for a path,
an end-relative seek for a seekable stream, and — free — the peek itself, since
`_peek_prefix` returns short when the file is smaller than the request, so any file below
`DETECTION_LIMIT` already reveals its exact size. Only a non-seekable stream longer than
the peek has no answer, and there the gate is skipped rather than guessed.

Note the decoder *already* enforces the mirror-image rule at the other end: `b"\x06"`
decodes to `b""` but `b"\x06" + anything` is rejected — trailing bytes after a finished
stream are an error. The bug is that the accepting paths declare lengths so large the
decoder never reaches the end within the prefix. The gate supplies the missing half.

## Rejected levers, with the numbers that killed them

| lever | why not |
| --- | --- |
| larger prefix | 4096-byte prefix: 7.94% vs 8.19%. The uncompressed path just gets more bytes to copy |
| require decoded output | ≥1 byte: 24/150 real streams missed. ≥512 bytes: 90/150. One-for-one trade |
| WBITS whitelist | `{22}` is 10.5× but rejects both real `.br` files on the measurement system (WBITS 15 and 16) and 4/12 WOFF2 fonts. The observed union `{15,16,19,22}` is worth 2.1%, because it must admit 16 — a **one-bit** encoding that alone is 50% of random data |
| first-byte legality table | 54/256 first bytes are provably impossible (including `\x7fELF`, which is why 0/887 ELF binaries were ever accepted). Free, but only 21% |
| extension gate / off by default | contradicts `VISION.md`'s founding use case ("wrong extensions are normal"); see proposal Decisions |

## What the gate does not fix

A crafted file genuinely *is* a valid Brotli stream — the investigation builds a 2 171 066-byte
one starting with `MZ` and round-trips it through the reference decoder. Brotli has no magic,
so "simultaneously valid Brotli and something else" is a property of the format. The residual
after the chain walk was 14 of 39 859 files on a real `/usr` tree (0.035%); that is the floor
this change reaches, and the honest-error requirement is what covers it.

## Implementation notes

- The header parser needs WBITS + one meta-block header and nothing else; anything reaching
  a Huffman body is "undecided, hand it to the decoder". Roughly 130 lines — a reference
  implementation with a six-vector self-test is in
  `scripts/exploration/brotli_probe_field_survey.py`.
- Watch the padding rules: an uncompressed meta-block must reach the byte boundary with
  zero bits, and the top MLEN nibble must be non-zero when `MNIBBLES > 4`. Both are load-
  bearing — they are exactly what rejects `MZ\x00…` and real PE DOS headers.
- A metadata meta-block **may** carry `ISLAST=1`; an early version of the parser rejected
  that and disagreed with the reference decoder on 13% of the metadata class.
- Bound the chain walk's link count. It is a file-driven loop over attacker-supplied
  offsets.
