# Process isolation overhead

What `process_isolation` and the rapidgzip child cost, measured on 2026-10-22, with a
follow-up on 2026-10-24. The handbook page that explains the mechanism is
[`topics/process-isolation.md`](../topics/process-isolation.md); it carries no numbers and
links here.

Host: 4-core Xeon 2.1 GHz (KVM), Linux, CPython 3.13.16, rapidgzip 0.16.0, main at
56de98f. The one-off measurement scripts were not committed; the maintained benchmarks are
`scripts/bench_process_isolation.py`, `scripts/bench_rapidgzip_child.py` and
`scripts/bench_bzip2_child.py`.

## Since this was measured

The 2026-10-22 numbers below predate these changes, so some of them are now too high:

- PR #1018 and #1019: the one-byte check at the open of a single `.gz` or `.bz2` uses the
  standard library, so a single `.gz` starts one rapidgzip child, not two.
- PR #1024: on POSIX the rapidgzip child reads a ZIP member's byte range itself from an
  inherited descriptor (`OPEN_RANGE`, `os.pread`). A full read of a 32 MiB member went from
  387 to 309 ms (the standard library: 222 ms).
- PR #1027: rapidgzip children are reused for the members of one open archive.
- The PPMd per-member figure in the `ProcessIsolation` docstring was corrected to the
  measured value.

### 2026-10-24: where a full read of a large ZIP member goes

A 40 MiB DEFLATE member with `seekable_members=True` (so `AUTO` picks rapidgzip), read in
full: about 580 ms, against about 300 ms with the accelerator off.

- **About 280 ms: a second decode with zlib from offset 0.** ZIP and 7z set
  `refuse_input_after_end`, so when rapidgzip's output ends the parent finds where the
  DEFLATE stream ends and whether bytes follow it (`_hands_over_at_end`, then
  `deflate_resume.stream_end`, in `zlib_codec.py`). A profile shows `zlib.decompress`
  taking 266 ms of the run.
- **About 95 ms:** opening the member and starting the child, up to the first byte.
- **About 200 ms:** the child's decode plus the copy back. rapidgzip alone, in-process
  with 4 threads, takes 112 ms, so the copy is about 90 ms.

Without the second decode a full read would be about level with the standard library.
On the same member, 20 random reads were 10 to 16 times faster with rapidgzip, and a full
read 1.4 to 1.8 times slower, for a path, an open file and a `BytesIO` alike.

## 2026-10-22 measurements

### Short answers

1. **Isolation on vs off, accelerator off (the default path):** `NONE` and
   `KNOWN_CRASHES` time the same on every gzip, bzip2, xz, zstd, ZIP and tar row,
   because the default isolates nothing on those paths. Only PPMd and rapidgzip move.
   `NATIVE_DECODERS` costs about **2.6 ms per MiB of output plus about 0.8 ms per
   member**: gzip 1.4 to 2x, zstd 2.4 to 5x, xz 1.2 to 1.6x, bzip2 about 1.05x.
2. **Fixed cost or throughput?** Almost all fixed. Every rapidgzip stream starts a
   new process: **about 37 ms per ZIP member** (deflate; 44 ms for bzip2), measured as
   the slope over 1 to 2048 members at a fixed 32 MiB. A single `.gz` pays **about
   70 ms**, because it starts **two** children: one for the one-byte check at open, one
   for the read. The throughput part is small: about 0.4 ms per MiB over in-process
   rapidgzip for a `.gz` file. So yes: a fast codec, or very compressible data, shows
   the same fixed cost as a much bigger multiplier. ZIP has a second problem (below).
3. **Why it wasn't noticed:** it was. The child came in **PR 493 (2026-09-26)**, not
   904, and 493's description measured it (`targz_read_all_accel_on` 19.4 → 95.6 ms,
   `zip_read_all_accel_on` 87 ms → 2.33 s) and raised the `AUTO` threshold to 16 MiB in
   response. Three things then kept it out of sight. `RESULTS.md` was never updated.
   The nightly flagged the tar.gz drift on 09-27, but the nightly had been red every
   night since at least 09-15. And the harness corpus compresses 250:1, which hides
   any gain. PR 904 only added the setting; rapidgzip children were already one per
   stream and stayed unpooled.
4. **rapidgzip for the index only, stdlib decoding from its points:** not faster for
   a full read. Building the index is a full rapidgzip decode, so you pay for the
   decode twice. In parallel threads it roughly ties today's child (116 vs 172 ms at
   16 MiB, 295 vs 282 ms at 64 MiB, ~550 vs 426 ms at 128 MiB). On one thread it is
   always slower than plain stdlib. For random access with dense points (256 KiB) it
   ties today's child too. It does not shrink the crash surface either, since
   rapidgzip still decodes everything to build the index. Not worth building now.

**An earlier summary** ("the slower rows come from forcing ON under 16 MiB, it's the
~45 ms start, the default is unaffected") is right for sequential reads and for
`.gz`/`.tar.gz`. It misses two things:
- **In-ZIP deflate never gains from rapidgzip, at any size, and the default does use
  it** when `seekable_members=True` and a member is ≥ 16 MiB compressed. A 32 MiB
  member (17 MB compressed) reads in 379 ms under `AUTO` + seekable, against 230 ms
  with `OFF`. Even rapidgzip **in-process** loses there (289 ms), because a ZIP member
  reaches rapidgzip as a Python stream (it asks the parent for every read and seek),
  not a path.
- The fixed cost per `.gz` is two children, not one (about 70 ms in total).

### What was measured

Each fixture × configuration runs in a fresh process: one cold read, then three warm
reads; the tables give the fastest warm read in ms. Each read takes every member to
its end in 1 MiB `read()` calls. Columns:

- `stdlib`: the stdlib peer (`gzip`/`bz2`/`lzma`/`zstd` module, `zipfile`, `tarfile`).
- `NONE off` / `KNOWN off` / `NATIVE off`: `process_isolation` = NONE /
  KNOWN_CRASHES (default) / NATIVE_DECODERS, with `use_rapidgzip` and
  `use_indexed_bzip2` = OFF.
- `KNOWN ON` / `NATIVE ON`: both accelerators forced ON. ON under NONE is refused by
  the config, so instead:
- `in-process ON`: KNOWN_CRASHES + ON, with `RapidgzipChildStream` monkeypatched to an
  in-process rapidgzip reader. This is an experiment, not reachable through the config,
  and it shows what the accelerator costs without the process.

Data is text-like (random words and numbers), about 2:1 for gzip and 3:1 for bzip2/xz.
That is closer to real files than the harness corpus (see Q3).

### Q1: isolation off vs on, across formats and sizes

Fastest warm read, ms.

#### Single .gz, size varies

| fixture | stdlib | NONE off | KNOWN off (default) | NATIVE off | KNOWN ON | NATIVE ON | in-process ON |
|---|---:|---:|---:|---:|---:|---:|---:|
| `single_1M.gz` | 8 | 9 | 9 | 13 | 76 | 78 | 10 |
| `single_4M.gz` | 33 | 32 | 32 | 75 | 119 | 121 | 35 |
| `single_16M.gz` | 120 | 115 | 114 | 192 | 183 | 179 | 88 |
| `single_64M.gz` | 519 | 483 | 456 | 662 | 289 | 278 | 161 |
| `single_128M.gz` | 977 | 885 | 913 | 1,255 | 434 | 434 | 316 |

#### Single .bz2, size varies

| fixture | stdlib | NONE off | KNOWN off (default) | NATIVE off | KNOWN ON | NATIVE ON | in-process ON |
|---|---:|---:|---:|---:|---:|---:|---:|
| `single_1M.bz2` | 47 | 98 | 97 | 106 | 220 | 189 | 131 |
| `single_4M.bz2` | 206 | 287 | 252 | 299 | 245 | 245 | 149 |
| `single_16M.bz2` | 847 | 884 | 848 | 905 | 451 | 445 | 310 |
| `single_32M.bz2` | 1,595 | 1,718 | 1,691 | 1,767 | 595 | 622 | 509 |

#### Single .xz and .zst (no accelerator)

| fixture | stdlib | NONE off | KNOWN off (default) | NATIVE off | KNOWN ON | NATIVE ON | in-process ON |
|---|---:|---:|---:|---:|---:|---:|---:|
| `single_1M.xz` | 23 | 28 | 26 | 29 | — | — | — |
| `single_4M.xz` | 84 | 90 | 93 | 148 | — | — | — |
| `single_16M.xz` | 323 | 345 | 357 | 441 | — | — | — |
| `single_32M.xz` | 680 | 685 | 676 | 797 | — | — | — |
| `single_1M.zst` | 2 | 6 | 5 | 10 | — | — | — |
| `single_4M.zst` | 7 | 10 | 10 | 53 | — | — | — |
| `single_16M.zst` | 26 | 32 | 31 | 97 | — | — | — |
| `single_64M.zst` | 98 | 107 | 109 | 259 | — | — | — |
| `single_128M.zst` | 183 | 219 | 222 | 520 | — | — | — |

#### ZIP deflate, 32 MiB total, member count varies

| fixture | stdlib | NONE off | KNOWN off (default) | NATIVE off | KNOWN ON | NATIVE ON | in-process ON |
|---|---:|---:|---:|---:|---:|---:|---:|
| `zipdefl_1x_32M.zip` | 216 | 245 | 226 | 334 | 401 | 521 | 289 |
| `zipdefl_16x_32M.zip` | 237 | 228 | 251 | 826 | 1,098 | 1,923 | 371 |
| `zipdefl_256x_32M.zip` | 256 | 302 | 294 | 578 | 9,857 | 10,057 | 647 |
| `zipdefl_2048x_32M.zip` | 264 | 617 | 610 | 2,087 | 75,595 | 75,847 | 2,840 |

#### ZIP deflate, 64 members, total size varies

| fixture | stdlib | NONE off | KNOWN off (default) | NATIVE off | KNOWN ON | NATIVE ON | in-process ON |
|---|---:|---:|---:|---:|---:|---:|---:|
| `zipdefl_64x_1M.zip` | 8 | 19 | 19 | 59 | 2,159 | 2,259 | 80 |
| `zipdefl_64x_4M.zip` | 33 | 41 | 47 | 99 | 2,419 | 2,295 | 119 |
| `zipdefl_64x_16M.zip` | 116 | 139 | 132 | 205 | 2,517 | 2,695 | 251 |
| `zipdefl_64x_64M.zip` | 466 | 456 | 475 | 677 | 3,253 | 3,369 | 790 |

#### ZIP bzip2, 32 MiB total, member count varies

| fixture | stdlib | NONE off | KNOWN off (default) | NATIVE off | KNOWN ON | NATIVE ON | in-process ON |
|---|---:|---:|---:|---:|---:|---:|---:|
| `zipbz2_1x_32M.zip` | 1,595 | 1,606 | 1,658 | 1,834 | 531 | 519 | 395 |
| `zipbz2_16x_32M.zip` | 1,583 | 1,715 | 1,660 | 1,646 | 1,727 | 1,646 | 835 |
| `zipbz2_256x_32M.zip` | 1,380 | 1,491 | 1,469 | 1,731 | 12,577 | 13,157 | 1,539 |
| `zipbz2_2048x_32M.zip` | 1,547 | 2,011 | 2,128 | 3,780 | 91,000 | 91,336 | 5,016 |

#### tar.gz, 32 MiB total, member count varies

| fixture | stdlib | NONE off | KNOWN off (default) | NATIVE off | KNOWN ON | NATIVE ON | in-process ON |
|---|---:|---:|---:|---:|---:|---:|---:|
| `tar_1x_32M.tar.gz` | 267 | 250 | 245 | 411 | 152 | 170 | 64 |
| `tar_16x_32M.tar.gz` | 287 | 267 | 276 | 449 | 165 | 165 | 96 |
| `tar_256x_32M.tar.gz` | 258 | 304 | 296 | 493 | 169 | 175 | 96 |
| `tar_2048x_32M.tar.gz` | 350 | 499 | 492 | 1,000 | 380 | 427 | 286 |

#### tar.gz, 64 members, total size varies

| fixture | stdlib | NONE off | KNOWN off (default) | NATIVE off | KNOWN ON | NATIVE ON | in-process ON |
|---|---:|---:|---:|---:|---:|---:|---:|
| `tar_64x_1M.tar.gz` | 13 | 17 | 17 | 62 | 49 | 50 | 15 |
| `tar_64x_4M.tar.gz` | 35 | 43 | 43 | 96 | 71 | 73 | 26 |
| `tar_64x_16M.tar.gz` | 139 | 142 | 134 | 264 | 118 | 121 | 65 |
| `tar_64x_64M.tar.gz` | 552 | 568 | 550 | 865 | 301 | 281 | 172 |

#### tar.bz2, 32 MiB total, member count varies

| fixture | stdlib | NONE off | KNOWN off (default) | NATIVE off | KNOWN ON | NATIVE ON | in-process ON |
|---|---:|---:|---:|---:|---:|---:|---:|
| `tar_1x_32M.tar.bz2` | 1,603 | 1,669 | 1,800 | 2,213 | 475 | 502 | 435 |
| `tar_16x_32M.tar.bz2` | 1,661 | 1,922 | 1,801 | 2,152 | 485 | 533 | 410 |
| `tar_256x_32M.tar.bz2` | 1,585 | 1,850 | 1,744 | 2,144 | 493 | 498 | 440 |
| `tar_2048x_32M.tar.bz2` | 1,964 | 2,088 | 2,209 | 3,127 | 651 | 690 | 568 |

#### 7z (no accelerator; PPMd is isolated under the default)

| fixture | stdlib | NONE off | KNOWN off (default) | NATIVE off | KNOWN ON | NATIVE ON | in-process ON |
|---|---:|---:|---:|---:|---:|---:|---:|
| `7z_lzma2_64x_16M.7z` | — | 358 | 362 | 534 | — | — | — |
| `7z_lzma2_nonsolid_2048x_8M.7z` | — | 868 | 921 | 2,231 | — | — | — |
| `7z_ppmd_solid_64x_16M.7z` | — | 2,953 | 2,967 | 3,021 | — | — | — |
| `7z_ppmd_nonsolid_2048x_8M.7z` | — | 2,551 | 5,531 | 5,437 | — | — | — |

How to read it:
- **KNOWN off = NONE off** everywhere except non-solid PPMd, within run-to-run noise
  (about ±7%). The default isolates nothing on gzip, bzip2, xz, zstd, ZIP or tar.
- **NATIVE off.** It costs about 2.6 ms per MiB of output on gzip (single .gz
  1→128 MiB: slope 9.8 vs 7.1 ms/MiB), plus 0.86 ms per ZIP member (2048 vs 1 member
  at 32 MiB). zstd decodes so fast that the copy dominates: 2.4x at 128 MiB, 5x at
  4 MiB. xz and bzip2 barely move. Each request carries about 500 KiB of output, so
  this is not small round trips. It is the copy through the pipe, at about 400 MB/s
  counting both directions. The design doc's "1.2 to 1.5 for zlib and about 5 for
  zstd" is roughly right for large single streams. tar.gz is 1.7 to 2x.
- **PPMd** (isolated under the default): a solid archive costs nothing extra. Non-solid,
  2048 members of 4 KiB: **5.5 s under KNOWN_CRASHES vs 2.6 s under NONE, +1.45 ms per
  member.** The design doc says "about 30 µs per small member". One identified part:
  the 64-decoders-per-child quota starts 32 new children for 2048 members (about 0.8 s).
  The rest is per-member request and lease cost that I did not split further.
  Non-solid 7z PPMd with thousands of members is rare, but the doc number is wrong by 50x.
- **Accelerator ON** wins for large `.gz`, `.tar.gz`, `.bz2`, `.tar.bz2` and for
  single-member ZIP bzip2. It loses for every ZIP deflate row, and for anything
  that starts many children.

### Q2: per-member overhead vs throughput

Linear fits from the two sweeps (member count at a fixed 32 MiB; size at a fixed
64 members or one stream):

| path | fixed per stream/member | per MiB of output |
|---|---:|---:|
| stdlib deflate in ZIP (KNOWN off) | 0.19 ms/member | 7.2 ms |
| NATIVE_DECODERS deflate in ZIP | 0.86 ms/member | 9.8 ms |
| rapidgzip child, ZIP deflate member (KNOWN ON) | **36.7 ms/member** | 17.4 ms |
| rapidgzip in-process, ZIP deflate member (experiment) | 1.25 ms/member | 11.3 ms |
| rapidgzip child, ZIP bzip2 member | **44 ms/member** | — |
| stdlib, single .gz | ~2 ms | 7.1 ms |
| rapidgzip child, single .gz | **~70 ms** (two children) | 2.8 ms |
| rapidgzip in-process, single .gz | ~8 ms | 2.4 ms |
| tar.gz member, any config | 0.11 to 0.12 ms/member (archivey's tar layer, one stream) | — |

**Where one child's time goes** (`breakdown.py`, which wraps every frame):

| step | cost |
|---|---:|
| `python -P` start | 15 ms |
| `import rapidgzip` | 8 ms |
| OPEN (pipes, threads, rapidgzip open), counted with the two above | 27 to 30 ms in total |
| reap at close | 1.5 to 3 ms (up to 47 ms after a 128 MiB stream) |
| READ round trip | ~70 µs, 1 MiB each; the pipe copy is about 0.4 ms/MiB over in-process |
| POINTS / OFFSETS index queries | ≤ 2.5 ms per stream, negligible |

- **Single `.gz` starts two children.** `SingleFileReader._validate_at_open`
  (`single_file_reader.py:253`) opens a full codec stream and reads one byte, to fail
  early on a file that isn't the claimed codec. Under ON (or AUTO + seekable on a big
  file) that stream is a rapidgzip child, which starts, begins its parallel decode,
  and is killed. The member read then starts a second one. Seen for `.gz` and `.bz2`;
  a `.tar.gz` starts one. The same probe likely explains a side finding with the
  accelerator off: a single `.bz2` is about 50 ms slower than the stdlib at 1 and
  4 MiB (97 vs 47 ms, 252 vs 206 ms). Checked by stubbing the check out: it accounts
  for about 22 ms of that gap (one bzip2 block decoded and thrown away; open 42 → 22 ms,
  full read of 1 MiB 94 → 68 ms). The remaining ~20 ms of the open is something else,
  not traced.
- **ZIP members are slow through rapidgzip even without the process.** A ZIP member
  is handed to rapidgzip as a stream, never a path. In the child, rapidgzip's threads
  ask the parent for each read and seek: 152 source reads and 154 seeks for one 32 MiB
  member. In-process the per-MiB cost is also above the stdlib's (11 to 17 vs
  7 ms/MiB), so a pool alone would not make ON win for ZIP. **Correction (follow-up, same day):** this is not Python callbacks as such. On the same 32 MiB member,
  rapidgzip in-process reads a plain `os.pread` range reader over the ZIP in 61 ms,
  a BytesIO copy in 55 ms and a memfd in 46 ms, against 184 ms for stdlib zlib and
  289 ms through archivey's member stream. The cost is archivey's member-stream
  layers.
- **The harness's ZIP row (64 × 256 KiB, 2.2 s)** is 64 × ~35 ms of child start-up,
  so it is nearly independent of size: 64 members take 2.16 s at 1 MiB in total and
  3.25 s at 64 MiB.

### Q3: why it wasn't noticed when rapidgzip moved to a child

- The move was **PR 493**, merged 2026-09-26 ("rapidgzip: decode gzip/zlib/deflate
  in a child process so a truncated stream cannot abort Python"). PR 904 (2026-10-20)
  added `process_isolation`. It left rapidgzip "one per stream, as today" (904 design,
  §rapidgzip) and made NONE refuse ON. It did not change rapidgzip's cost.
- 493 measured and stated the cost: start-up 25 ms; full read through the codec
  246 → 332 ms; `targz_read_all_accel_on` 19.4 → 95.6 ms; `zip_read_all_accel_on`
  87 ms → 2.33 s, "starts one child per member"; "the nightly wall-drift check will
  flag them". Its answer was the 16 MiB `AUTO` threshold, from a 13 MB break-even.
  The trade (crash safety over forced-ON speed) was deliberate.
- What hid it afterwards:
  - `benchmarks/RESULTS.md` still shows the in-process numbers (tar.gz ON 2.57x
    faster, tar.bz2 2.97x). Nobody re-measured the accelerator section after 493, and
    #907 (10-20) re-baselined the peers, not the ON rows.
  - The nightly (`benchmark-wall.yml`) did flag it. Run 36317711033 on 09-27 lists
    `targz_read_all_accel_on: wall_ratio=2.18 drifted from previous 0.43`. But that
    run had eight other drift failures, and the nightly failed every night from at
    least 09-15 to 10-04, so one more red line went unseen.
  - `zip_read_all_accel_on` has no stdlib peer, so it has no ratio and no drift gate.
    1.744 s sat in the "All cases" table with no flag.
  - The harness corpus compresses about 250:1 (32 MiB of gzip is 130 KB). On it, the
    stdlib decodes at 0.5 ms/MiB. In-process rapidgzip is already 3.6x slower than
    archivey with the accelerator off (54 vs 15 ms), and the child is 11x slower
    (166 ms). Those rows can only ever show fixed costs, never where the
    accelerator pays.

### Q4: rapidgzip only for the index, stdlib from its points

Prototype scripts (not committed). The stdlib decodes from
each point with archivey's own `DeflateResume` (preset window + the bit-offset splice),
in one thread or four. The output is CRC-checked against the gzip trailer (it matches).

**Full read of a single `.gz`, ms:**

| file | stdlib | rapidgzip in-process | today's child (ON) | index in a child (spawn + index + `export_index` back) | + stdlib from points, 1 thread | + stdlib from points, 4 threads |
|---|---:|---:|---:|---:|---:|---:|
| 16 MiB | 114 | 42 | 172 | 68 | 68 + 112 = 180 | 68 + 48 = **116** |
| 64 MiB | 441 | 94 | 248 to 282 | 120 | 120 + 423 = 543 | 120 + 163 = **283** |
| 128 MiB | 937 | 203 | 426 | 211 | 211 + 891 = 1102 | 211 + 339 = **550** |

- One thread: always slower than the stdlib alone. The index costs a full decode.
- Four threads: about the same as today's child. Better at 16 MiB (no output through
  the pipe, one child instead of two), worse at 128 MiB (four zlib threads at
  2.6 ms/MiB are slower than rapidgzip plus a 0.4 ms/MiB copy).
- The index with windows is small (about 8 KiB per MiB at the default 4 MiB chunk
  spacing) and cheap to send.

**Random access** (64 reads of 64 KiB at random offsets after the open):

| file | today's child | index+stdlib, 4 MiB points | 1 MiB points | 256 KiB points |
|---|---:|---:|---:|---:|
| 16 MiB | 200 | 988 | 350 | **186** |
| 64 MiB | 426 | 1298 | 494 | **412** |

With dense points (`RapidgzipFile(chunk_size=256 KiB)`; windows then add 12% of the
output in memory) it ties today's child, with nothing left running after the index.
With rapidgzip's default spacing it is 3 to 5x slower.

So "index only" buys neither a full-read nor a seek speedup. Its real properties are
elsewhere: the child exits after the index, and later reads are stdlib in-process.
It does **not** reduce crash exposure, because the index pass is still a full
rapidgzip decode of the untrusted stream. A resumed decode also cannot check the
gzip CRC by itself, so the CRC needs a sequential pass or a CRC combine. Not worth
building for speed. The cheaper wins were the open check, the child pool and the file
range, all since done (see "Since this was measured").
