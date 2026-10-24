# Process isolation overhead

What `process_isolation` and the rapidgzip child cost today. The handbook page that
explains the mechanism is [`topics/process-isolation.md`](../topics/process-isolation.md);
it carries no numbers and links here.

Every figure on this page comes from two committed scripts, run on main at `f347a2a0` on
2026-10-24. To refresh the page, run them again and replace the tables:

```bash
uv run --no-sync python scripts/bench_process_isolation.py   # §1
uv run --no-sync python scripts/bench_accelerator_reads.py   # §2 and §3
```

Host: 4-core Xeon 2.1 GHz (KVM), Linux, CPython 3.11.17, rapidgzip 0.16.0. Each case runs
in a fresh interpreter: one warm-up read, then the fastest of three. The payload is random
words and numbers, which gzip compresses about two to one. Between runs the same case
moves by up to about 20% on the small rows, so treat a difference under that as noise.

## Summary

- **The default isolates only PPMd.** `KNOWN_CRASHES` reads gzip, bzip2, xz, zstd, ZIP
  and LZMA2 as fast as `NONE`. PPMd costs about 1 ms more per decoder, so a non-solid 7z
  of many small PPMd members is about twice as slow, and a solid one about 20% slower.
- **`NATIVE_DECODERS` costs a copy through a pipe:** about 3.5 ms per MiB of output, plus
  about 0.5 to 0.8 ms per decoder. gzip and xz are 1.2 to 1.6 times slower, zstd 3 to 4
  times, and a ZIP of 1024 small members about 5 times.
- **One rapidgzip stream costs about 36 ms more than the standard library** for its
  start. Within one archive the child is reused, so each further ZIP member costs about
  3.4 ms under `ON`.
- **The accelerators pay off for large gzip and bzip2 data** read with seeking allowed:
  a 64 MiB `.gz` reads in 181 ms against 455, a 16 MiB `.bz2` in 305 ms against 740.
- **A large DEFLATE member in a ZIP reads slower through rapidgzip**: 396 ms against 297
  for 40 MiB, because the end check decodes the member a second time with zlib (§3).
  Random reads in the same member are about 24 times faster.

## 1. Isolation, accelerators off

`scripts/bench_process_isolation.py`: each archive read in full with `stream_members()`,
`use_rapidgzip` and `use_indexed_bzip2` set to `OFF`. Times in ms, and the ratio to
`NONE`.

| archive | `NONE` | `KNOWN_CRASHES` (default) | `NATIVE_DECODERS` |
|---|---:|---:|---:|
| `01M.gz` | 7 | 8 (1.17x) | 14 (1.87x) |
| `16M.gz` | 120 | 146 (1.22x) | 191 (1.59x) |
| `64M.gz` | 454 | 475 (1.05x) | 678 (1.49x) |
| `01M.zst` | 5 | 5 (1.09x) | 9 (2.08x) |
| `16M.zst` | 28 | 28 (1.02x) | 108 (3.90x) |
| `64M.zst` | 111 | 106 (0.95x) | 342 (3.07x) |
| `01M.bz2` | 90 | 112 (1.24x) | 98 (1.08x) |
| `16M.bz2` | 812 | 804 (0.99x) | 950 (1.17x) |
| `01M.xz` | 28 | 32 (1.13x) | 31 (1.08x) |
| `16M.xz` | 345 | 327 (0.95x) | 404 (1.17x) |
| `deflate-1x-8M.zip` | 56 | 54 (0.97x) | 112 (1.98x) |
| `deflate-1024x-8M.zip` | 224 | 229 (1.02x) | 1,096 (4.88x) |
| `lzma2-nonsolid-1024x4K.7z` | 449 | 425 (0.95x) | 1,196 (2.66x) |
| `lzma2-solid-1024x4K.7z` | 151 | 157 (1.03x) | 378 (2.50x) |
| `ppmd-nonsolid-1024x4K.7z` | 1,491 | 2,783 (1.87x) | 2,943 (1.97x) |
| `ppmd-solid-1024x4K.7z` | 1,125 | 1,350 (1.20x) | 1,301 (1.16x) |

The script's derived figures: the extra time of the many-decoder archive over its
one-decoder pair, per extra decoder, and a solid folder's extra time per member read. A
"control" row is a codec the setting leaves in this process, so its figure is the noise.

| setting | ZIP deflate, per decoder | 7z LZMA2, per decoder | 7z PPMd, per decoder | LZMA2 solid, per member | PPMd solid, per member |
|---|---:|---:|---:|---:|---:|
| `KNOWN_CRASHES` | 0.01 ms (control) | -0.03 ms (control) | 1.04 ms | 0.01 ms (control) | 0.22 ms |
| `NATIVE_DECODERS` | 0.80 ms | 0.51 ms | 1.25 ms | 0.22 ms | 0.17 ms |

How to read it:

- **`KNOWN_CRASHES` equals `NONE` within noise** on every row but PPMd. The 1 MiB `.gz`
  and `.bz2` rows differ by a few ms, about the noise between runs at that size.
- **`NATIVE_DECODERS` per MiB.** Fitting the `.gz` rows (1 to 64 MiB): about 7 ms per
  MiB under `NONE` and about 10.5 under `NATIVE_DECODERS`, so about 3.5 ms per MiB for the
  copy. zstd decodes so fast that the copy dominates, hence its 3 to 4 times. bzip2 and
  xz decode slowly enough that the copy hardly shows.
- **PPMd** runs in a child under the default. It pays about 1 ms per decoder, which a
  non-solid archive pays once per member.

## 2. The accelerators

`scripts/bench_accelerator_reads.py`, part 1: each archive read to the end of every
member under `KNOWN_CRASHES`. `stdlib` is the standard-library module alone; `OFF` sets
both accelerators `OFF`; `AUTO` is the default config. `OFF` and `AUTO` read with
`stream_members()`. `AUTO seek` and `ON seek` open the archive with
`seekable_members=True` and read each member through `open()`; `ON` forces both
accelerators on. Fastest of three, ms.

| archive | stdlib | `OFF` | `AUTO` | `AUTO seek` | `ON seek` |
|---|---:|---:|---:|---:|---:|
| `01M.gz` | 7 | 9 | 10 | 9 | 43 |
| `16M.gz` | 110 | 115 | 112 | 112 | 96 |
| `64M.gz` | 466 | 455 | 451 | 181 | 195 |
| `01M.bz2` | 48 | 113 | 92 | 90 | 137 |
| `16M.bz2` | 781 | 740 | 813 | 305 | 294 |
| `deflate-1x-32M.zip` | 188 | 237 | 238 | 343 | 358 |
| `deflate-16x-32M.zip` | 215 | 244 | 265 | 231 | 485 |
| `deflate-256x-32M.zip` | 250 | 259 | 269 | 252 | 1,229 |
| `bzip2-1x-16M.zip` | 758 | 767 | 794 | 256 | 273 |
| `1x-32M.tar.gz` | 221 | 249 | 249 | 246 | 247 |
| `1024x-32M.tar.gz` | 262 | 336 | 330 | 683 | 644 |
| `1x-16M.tar.bz2` | 790 | 764 | 850 | 494 | 543 |

The script's derived figures:

| figure | value |
|---|---:|
| One rapidgzip stream beyond the standard library (`01M.gz`, `ON seek` minus `stdlib`) | 36 ms |
| Each further ZIP member under `OFF` (deflate, 1 to 256 members at 32 MiB) | 0.09 ms |
| Each further ZIP member under `ON seek`, one child reused for the archive | 3.42 ms |

How to read it:

- **`AUTO` without seeking is `OFF`.** `AUTO` uses an accelerator only where seeking is
  declared, so a `stream_members()` read never starts a child.
- **`AUTO seek` starts a child only above the size threshold** (16 MiB compressed for
  DEFLATE, 1 MiB for bzip2), so it follows `OFF` on the small rows and `ON seek` on the
  large ones. Where it starts one: large `.gz` and `.bz2` data, and a ZIP bzip2 member,
  read 2.5 to 3 times faster; a large ZIP DEFLATE member reads slower (§3).
- **`ON seek` on small data** pays the child's start: 43 against 7 ms for 1 MiB of gzip.
  On many ZIP members it pays the per-member cost above, which is why 256 members take
  1.2 s against 0.26 s.
- **The `.tar.gz` of 1024 members** reads slower with seeking allowed, under `AUTO`
  (683 ms) and `ON` (644 ms) alike, than sequentially (330 ms). Measured, not traced.
- **A 1 MiB `.bz2` under `OFF`** is about twice the standard library (113 against
  48 ms), with no child involved. Not traced.

## 3. One large ZIP DEFLATE member

`scripts/bench_accelerator_reads.py`, parts 2 and 3: one 40 MiB DEFLATE member, opened
with `seekable_members=True`, so `AUTO` reads it through the rapidgzip child.

| measurement | ms |
|---|---:|
| Full read, `AUTO` (rapidgzip child) | 396 |
| Full read, `OFF` (standard-library zlib) | 297 |
| `AUTO`: open and first byte | 90 |
| A plain `zlib` decode of the member's raw data | 263 |
| 20 random reads of 64 KiB after the open, `AUTO` | 78 |
| 20 random reads of 64 KiB after the open, `OFF` | 1,860 |

Why the full read is slower: ZIP and 7z set `refuse_input_after_end`, so when rapidgzip's
output ends, the parent finds where the DEFLATE stream ends and whether bytes follow it by
decoding the member again with zlib from offset 0 (`_hands_over_at_end`, then
`deflate_resume.stream_end`, in `zlib_codec.py`). That second decode costs about what the
plain zlib row shows, which is more than the whole gap between `AUTO` and `OFF`. Without
it, the full read through the child would be about level with the standard library or
faster. The random reads are where the child pays: about 24 times faster than `OFF`,
which must decode from the start of the member for every seek back.
