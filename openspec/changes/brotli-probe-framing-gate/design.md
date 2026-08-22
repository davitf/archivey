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

### Decision (2026-08-22): ship first-block only; defer the chain walk

Maintainer ruling while applying: **this change implements the first-block framing check
only.** The chain walk stays documented and measured, but is **out of scope** here.

Why: the probe interface is `prefix` + optional `source_length`. That is enough for
`header + declared_length <= source_length` on the first meta-block, with no extra I/O.
The chain walk needs bytes at successor offsets that often lie past the peeked prefix
(OLE's next header is ~7425). Putting those reads inside the probe would break the
"MUST NOT read beyond the prefix" rule; putting them in the detector is a second
shape that deserves its own change once the first-block gate has landed.

Consequence for residual numbers claimed by *this* change: on the measured `/usr` tree
the first-block check alone rejects 1316 of 1377 probe hits (leaves **61**, ≈0.15%); the
chain walk's further cut to 14 (0.035%) is the follow-up's win, not this one's. Random-blob
rates at large sizes stay closer to today's until the walk lands — at 16 MiB first-block
is vacuous. Zero false negatives is unchanged: every complete valid stream still satisfies
the first-block invariant.

Follow-up home: task **5.7** (was 2.3). Bound, ownership (detector vs richer probe
callback), and accept-vs-reject on link-cap remain open until that change.

### Decision (2026-08-22): probe-only decode failure = same type + flag + new diagnostic

Maintainer ruling while applying: when a probe-only (`GUESS`) single-file read fails:

- **Keep** `TruncatedError` / `CorruptionError` (no new subclass — `except TruncatedError`
  must still catch).
- **Rewrite** the message so it names unconfirmed identification and does not claim zero
  output was produced.
- Add `ArchiveyError.format_unconfirmed: bool = False`, set `True` on these raises;
  `__str__` appends `format_unconfirmed=True`. Fits the existing raw-attribute pattern
  (`source_format`, `member_name`, …) better than a new type or stuffing
  `DetectionConfidence` onto every error.
- Emit a **new** diagnostic code `PROBE_FORMAT_UNCONFIRMED` with
  `UnconfirmedFormatContext.chosen_by="content_probe"`. Do **not** reuse
  `EXTENSION_FORMAT_UNCONFIRMED` (that keys on empty listing + extension fallback).

Corroborated (`.br` / `PROBABLE`) failures stay unchanged: `format_unconfirmed=False`,
today's message, no probe-unconfirmed diagnostic.

### Decision (2026-08-22): compressed-first probe-only keeps PROBABLE

Maintainer ruling while applying: implement task **3.1a**. A Brotli content-probe match
whose **first meta-block is compressed** reports `PROBABLE` even without a corroborating
extension; uncompressed- or metadata-first probe-only hits stay `GUESS`.

Why now (tied to the exception flag): `format_unconfirmed` / `PROBE_FORMAT_UNCONFIRMED`
fire only on **GUESS**. So GUESS vs PROBABLE is not a cosmetic `FormatInfo` label — it
gates whether a later decode failure is stamped as "format may have been wrong."
Compressed-first measured ~0.014% acceptance on random data (vs ~100% for
uncompressed-first) and 25/25 wild streams; that evidence is strong enough that those
hits should take the corroborated failure path, not the unconfirmed one.

Pin against drift: uncompressed-first remains **valid** (incompressible /
already-compressed payloads). The framing gate keeps those streams; they must stay
accepted. They report `GUESS` when extensionless, which is the honest residual class —
not a claim that uncompressed-first is illegal.

## Why the probe needs the source length

`content_probe(prefix: bytes) -> bool` cannot see it. Detection already can, via the
existing `source_byte_size()` helper (`streams/streamtools/binaryio.py`): path → `stat`,
then a stream's `.size`, then `try_get_size()`, then a cheap `SEEK_END` only when that
seek is O(1). Do **not** reimplement that dispatch beside `_peek_prefix`. When
`source_byte_size` returns `None` (non-seekable / unknown length), the gate is skipped
rather than guessed.

Note the decoder *already* enforces the mirror-image rule at the other end: `b"\x06"`
decodes to `b""` but `b"\x06" + anything` is rejected — trailing bytes after a finished
stream are an error. The bug is that the accepting paths declare lengths so large the
decoder never reaches the end within the prefix. The gate supplies the missing half.

## Rejected levers, with the numbers that killed them

| lever | why not |
| --- | --- |
| larger prefix | 4096-byte prefix: 7.94% vs 8.19%. The uncompressed path just gets more bytes to copy |
| require decoded output | ≥1 byte: 24/150 real streams missed. ≥512 bytes: 90/150. One-for-one trade |
| WBITS whitelist | `{22}` is 10.5× but rejects both real `.br` files on the measurement system (WBITS 15 and 16) and 4/12 WOFF2 fonts. An observed union is worth only 2.1%, because it must admit 16 — a **one-bit** encoding that alone is 50% of random data. And "observed" is not a stable set: this machine's `{15,16,19,22}` did not survive one independent run, which added 18 and 21 from real Fira Sans / Source Code Pro. The union across two machines is now `{15,16,18,19,21,22}` — six of fifteen legal values, from two samples that disagreed on first contact (results doc §4.1) |
| first-byte legality table | 54/256 first bytes are provably impossible (including `\x7fELF`, which is why 0/887 ELF binaries were ever accepted). Free, but only 21% |
| extension gate / off by default | contradicts `VISION.md`'s founding use case ("wrong extensions are normal"); see proposal Decisions |

## A lever adjacent to the gate, deliberately not taken

The residual is not only lucky fits. **OLE/CFB** (`D0 CF 11 E0 A1 B1 1A E1` — `.doc`,
`.xls`, `.msi`, `.vsmacros`) has a *constant* 8-byte magic that parses as WBITS 16,
MLEN 7422, 3-byte header, so **every CFB file above 7425 bytes passes both the framing
check and the chain walk**. COFF objects (`64 86 …`) behave the same way.

A short denylist of such container magics would catch them for a few bytes of work. It is
**not** the first-byte legality table §4.1 rejected: those 54 bytes *cannot* begin a valid
stream, whereas OLE magic *can*, so a denylist is a heuristic with a real (if absurd)
false-negative — a crafted Brotli stream that starts with OLE magic.

Not proposed here, on purpose: this change's value is that its rule is *sound*, and mixing
in a heuristic denylist would forfeit that property for two file families. The residual is
named in `format-detection`'s framing-gate scenario table and fixtured in task 4.3 instead,
so it is tested rather than anecdotal. If `.doc`/`.msi` falling out of `detect_format`
becomes worth having, it belongs in its own change alongside the extension-first ordering
work (task 5.1), where heuristics are the point.

## What the gate does not fix

A crafted file genuinely *is* a valid Brotli stream — the investigation builds a 2 171 066-byte
one starting with `MZ` and round-trips it through the reference decoder. Brotli has no magic,
so "simultaneously valid Brotli and something else" is a property of the format. After the
**first-block** check, 61 of 39 859 files on a real `/usr` tree still hit (~0.15%); the
deferred chain walk would cut that to 14 (0.035%). That residual, and the honest-error
requirement, are what cover what this change leaves.

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
- **No chain walk in this change** (see Decision above). When the follow-up lands, bound
  its link count — it is a file-driven loop over attacker-supplied offsets. The survey
  script's reference is `chain_walk(..., max_links=64)` in
  `scripts/exploration/brotli_probe_field_survey.py`.
