# Investigation brief — why the Brotli content probe accepts arbitrary data

**Status:** open. Written 2026-08-19 while implementing `sfx-format-detection`; the
maintainer asked for a dedicated deep dive rather than folding a guess into that change.
Results belong next to this file as `brotli-content-probe-results.md`.

**Who this is for:** an agent or contributor with a few hours, willing to read the Brotli
specification (RFC 7932) and the `brotli` / `brotlicffi` C sources, not just measure the
Python API from outside. The measurements below already exist — the point of the brief is
to explain *why* they come out this way and what, if anything, can be done about it.

---

## What we know

`archivey` detects Brotli by content probe, because the format has no magic number at
all (`BrotliCodec.content_probe` → `StreamCodec._decodes_sample`, `codecs.py`). The probe
feeds the first `_PROBE_PREFIX = 256` bytes through the decoder and accepts the stream if
the decode either produces output or raises `TruncatedError` (ran out of the bounded
prefix). That probe is the last thing consulted before the ISO window and the extension
guess, so whatever it accepts becomes the answer.

Measured on this repo at commit `d43375f` (scripts under
`/tmp/.../scratchpad`, reproducible in a few lines — see *Reproducing* below):

| Input | Probe says |
| --- | --- |
| Uniformly random 4 KiB blobs | **8.27%** accepted as Brotli (`detect_format` end-to-end: 146/2000 = 7.3%) |
| `b"MZ" + b"\x90" * 4094` | accepted — decodes **252 bytes** cleanly, not a truncation |
| `b"MZ" + b"\x00" * 4094` | rejected |
| `b"MZ" + b"A" * 4094` | rejected |
| 887 real ELF binaries (`/usr/bin`, `/usr/lib`) | **0** accepted |
| A structurally valid PE stub (DOS header, `e_lfanew` → `PE\0\0`) | rejected, with every filler tried |
| Real `rar a -sfx` ELF stub (249 KB) | rejected |

And the mitigations that do **not** work, measured against 15 real Brotli streams
(qualities 1/5/11 over text, random bytes, an ELF binary, an empty payload, one byte):

| Probe variant | Random-data FP | Real-Brotli misses |
| --- | --- | --- |
| 256-byte prefix, any outcome (today) | 8.27% | 0/15 |
| 256-byte prefix, require ≥1 byte out | 8.27% | 3/15 |
| 256-byte prefix, require ≥512 bytes out | 1.60% | 10/15 |
| 1024-byte prefix, any outcome | 8.27% | 0/15 |
| 4096-byte prefix, any outcome | 8.20% | 0/15 |
| 4096-byte prefix, require ≥512 bytes out | 8.13% | 6/15 |

So: **a bigger prefix buys nothing, and requiring output trades the false positives for
false negatives at roughly one-for-one.** Probe tuning is a dead end. That conclusion is
what `sfx-format-detection` acted on — it fixed the SFX case by scanning for archive
magic *before* the probes rather than by making the probe stricter — and it is why the
residual needs its own investigation instead of a guess.

## Why it matters

`open_archive` on a file the probe wrongly claims does not fail. It returns a
`SingleFileReader` with one fabricated member named `<filename>.uncompressed`. That is a
silent wrong answer on attacker-supplied bytes, which `VISION.md` ranks above almost
everything else, and it is registered as `open-issues.md` P12 and `threat-model.md` O10.

## Questions to answer

1. **Why does `b"MZ" + b"\x90"*4094` decode?** Walk the first bytes as RFC 7932 sees
   them: `WBITS`, `ISLAST`, `MNIBBLES`, then the meta-block header. Which fields do
   `0x4D 0x5A 0x90 0x90 …` land on, and what makes the decoder emit 252 bytes rather
   than erroring? Contrast with `b"MZ" + b"\x00"*4094`, which is rejected — that pair
   isolates the mechanism better than any amount of statistics.
2. **What is the real acceptance rate, analytically?** ~8% of uniformly random data
   is a large number. Derive (or bound) it from the header layout: how many bits of the
   prefix are actually constrained, and how much of the apparent looseness is the
   decoder tolerating a stream it has not yet found invalid? Does the rate drop with
   more *decoded output demanded* only because most accidental streams are short?
3. **Can a legal Brotli stream begin with `MZ`?** `sfx-format-detection` deliberately
   left a weak `MZ` prefix able to reach the content probes, on the grounds that two
   bytes prove nothing and a real Brotli stream might start with them. Is that actually
   possible? Enumerate the legal first bytes: `WBITS` occupies the low bits of byte 0,
   so some values may be reserved or impossible. If `M` (`0x4D`) cannot legally start a
   Brotli stream, the SFX cue can be tightened from STRONG-only to any `MZ` prefix, and
   the residual in question 5 mostly disappears. Same question for `\x7fELF`.
4. **Is there a cheap structural pre-gate at all?** zlib's probe gates on its two-byte
   CMF/FLG header before decoding (`_ZLIB_HEADERS`), and LZMA Alone gates on a plausible
   13-byte header (`_alone_header_plausible`). Brotli has nothing equivalent today. Is
   there *any* constraint — reserved `WBITS` values, meta-block length sanity, a
   distance/context-map field that is over-constrained in practice — that rejects a
   meaningful share of random data without rejecting real streams? A gate that halves
   the FP rate at zero FN cost is worth having even if it does not close the gap.
5. **What should detection do with what is left?** Options, none pre-selected:
   - keep the probe but require extension corroboration (`.br`) for a positive
     auto-detect, reporting `GUESS` rather than `PROBABLE` otherwise;
   - keep the probe but demand more of the *decoded* bytes than "some" — e.g. that the
     decoder reaches a meta-block boundary rather than merely producing output;
   - let `open_archive` fail loudly on a single-file result whose only evidence is a
     content probe, instead of fabricating a member;
   - accept the rate and document it.
   Each option has an obvious cost; the deep dive should say which costs are real. Note
   that whatever lands must keep bare `.br` files working — they are a supported format,
   not an edge case (`format-single-file-compressors`).
6. **Do the peer probes share the problem?** Run the same random-data measurement
   against zlib and LZMA Alone. Both gate on a header first, so the expectation is a far
   lower rate — but "expectation" is what this brief exists to replace. If either is also
   loose, the fix belongs at the probe layer generally rather than in Brotli alone.

## Reproducing

```python
import random
from archivey.internal.streams.codecs import _BY_STREAM_FORMAT
from archivey.types import StreamFormat

codec = _BY_STREAM_FORMAT[StreamFormat.BROTLI]
rnd = random.Random(42)
hits = sum(
    codec.content_probe(bytes(rnd.randrange(256) for _ in range(300)))
    for _ in range(3000)
)
print(hits / 3000)  # ~0.076
```

End-to-end (what a user would hit) is `detect_format(io.BytesIO(random_bytes))` in a
loop; count the results that come back `ArchiveFormat.BROTLI`.

## Boundaries

- **Not** a request to change detection in passing. Land findings as a written result
  plus, if a change is warranted, an OpenSpec proposal — the probe's behaviour is
  normative in `format-detection` and any change moves what bare `.br` files detect as.
- Keep the SFX work out of scope: `sfx-format-detection` already closed the
  archive-behind-a-stub path, and it did so without touching the probe precisely so this
  investigation stays free to change it.

## Refs

- `src/archivey/internal/streams/codecs.py` — `_PROBE_PREFIX`, `_decodes_sample`,
  `BrotliCodec.content_probe`, and the zlib / LZMA-Alone gates to compare against
- `src/archivey/internal/detection.py` — where the probe result becomes the answer
- `openspec/specs/format-detection/spec.md` — the normative probe requirements
- `openspec/changes/archive/…/sfx-format-detection/design.md` — the measurements above in
  their original context, and why scan-before-probe was chosen over probe tuning
- `dev-docs/open-issues.md` P12, `dev-docs/threat-model.md` O10
- RFC 7932 §9 (stream format), and `brotli`'s `state.c` / `decode.c`
