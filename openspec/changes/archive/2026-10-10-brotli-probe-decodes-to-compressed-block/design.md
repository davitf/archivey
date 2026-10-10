# Design: Brotli chain decode

## Measurements (2026-10-10)

Random blobs, `detect_format` on a `BytesIO` with no name, `BALANCED`:

| Size | Seeds | Claimed before | Claimed after |
| --- | --- | --- | --- |
| 65 537 B | 5 000 | 58 | 1 |
| 256 KiB | 10 000 | 137 | 0 |
| 1 MiB | 2 000 | 24 | 0 |
| 4 MiB | 1 000 | 13 | 3 |

The one left at 65 537 B is a different class: its first meta-block is compressed and
the whole source decodes without error, so neither the walk nor this decode applies. The
three at 4 MiB have their compressed block at 1.39, 2.44 and 3.75 MiB, past the 1 MiB
reach, and are kept as *cannot disprove*.

The report's probe script, rerun on a fresh libmagic `Magdir` (3 143 signatures, 18 868
bodies) and the 10 claimed real files: Brotli claims went from 102 bodies (68 signatures)
and 5 real files to none. The Brotli probe called directly (no budget) went from 120
accepts to none. LZMA Alone claims are unchanged (710 bodies).

Real streams: 343 `brotli.compress` outputs of random-then-text payloads (3 KiB to
1.1 MiB of noise, qualities 0–11, `lgwin` 10–24); 98 have a compressed block past the
window. None is missed.

Cost (one core, brotli 1.2.0, best of 7):

| Case | Probe before | Probe after | `detect_format` on a path |
| --- | --- | --- | --- |
| Fabricated, compressed block at 270 KiB (`.pyc` size), rejected | 0.08 ms | 3.8 ms | 5.5 ms |
| Fabricated, compressed block at 1 MiB − 20 KiB (largest `BALANCED` decodes), rejected | 0.07 ms | 2.3 ms | 3.0 ms |
| Real stream, 900 KiB uncompressed then text, accepted | 0.10 ms | 1.8 ms | 2.5 ms |

Before the 4 KiB sample reads, the first row took 300 ms and a 1 MiB rejection about a
second: the decoder's replay handed the failing 64 KiB read over one byte at a time.

## Decisions

- **Decode from offset 0, not from the compressed block.** `brotli` cannot start a
  decoder mid-stream, and the bytes before the block are mostly copied. The read is
  sequential, which suits remote sources.
- **Margin 4 KiB.** Every measured failure was within 256 bytes of the header. A real
  stream that reaches the end of the sample is accepted, so a larger margin only costs
  time.
- **Charged, and bounded by the read ceiling.** `detection-cost` says every tier that
  decodes draws on `max_decode_input`. The read ceiling bound keeps a custom budget with a
  large decode allowance and a small scan window from reading past what it allows
  elsewhere.
- **`charge_decode` is a probe argument**, like `read_at`, so the codec layer does not
  depend on the detection workspace. Probes that do not decode past their sample accept
  it and ignore it.
- **No source length in the widened sample.** With it, a sample that ends at EOF would
  get the 64 KiB completeness drain, which the uncompressed copy alone can fill before
  the decoder reaches the compressed block.
