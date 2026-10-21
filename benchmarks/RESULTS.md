# Benchmark gate — sample results

**Blocking PR gate = structural invariants only** (seek-count baselines + solid
decode-once). Wall-time / VISION ≤1.3× runs off the PR path as a **change-guarded
nightly** (`benchmark-wall.yml`): a daily schedule whose expensive realistic run
fires when the default branch changed recently **or** the last measurement is
older than ~30 days (runner / toolchain drift). Quiet days **re-publish** the
previous JSON (preserving `measured_at`, stamping `republished_at`) so dormant
successes still carry an artifact for the next drift compare. When the expensive
run fires it:

- records results (JSON + markdown report artifacts, job summary table) with
  `measured_at` / source provenance;
- hard-fails on the ~10× sanity ceiling **or** on **wall-ratio drift** vs the
  JSON of the last run that measured, pass or fail (relative regression gate —
  debt-ledger Q1 / perf Q2 option (a)). A case drifts when its ratio rises to
  more than 1.25× the baseline ratio **and** by at least +0.15 in absolute terms
  **and** the rise costs ≥1 ms of wall time at this run's stdlib speed, so
  sub-millisecond listing cases do not fail on timer noise. Because a failed run's
  JSON becomes the next baseline, a real regression fails the first night that
  measures it and not every night after;
- prints absolute VISION / Q1 listing bands as informational only.

**Maintainer decision (davitf, 2026-10-10, #655):** compare against the last
measured run, pass or fail, and add the 1 ms floor. This supersedes the
"previous successful nightly" baseline of performance review Q2 (decided
2026-07-20). Reasons: on 2026-09-07 one sub-millisecond case failed on timer noise
(`rar_open_list`, 1.0 ms vs a 0.2 ms peer, 2.60× → 5.71×). With a successful-only
baseline that one night froze the 2026-09-06 numbers until 2026-10-09, and every
later change added up against them until each measured night failed. The cost: a
real regression is red for one night, then becomes the baseline unless someone
acts on that night's failure. Reopen if a regression is accepted that way without
anyone seeing it, or if a sub-millisecond case shows a real regression the 1 ms
floor hides.

`workflow_dispatch` can pass `skip_drift=true` to accept the current ratios as a
new baseline after an intentional slowdown.

Re-run with:

```bash
# PR structural gate (small fixtures, fast)
uv run --extra all python -m benchmarks.harness --mode structural --scale ci

# Realistic wall-time vs stdlib (multi-MiB corpora; interleaved medians)
uv run --extra all python -m benchmarks.harness --mode full --scale realistic
```

**Measurement host for tables below:** 4-core Intel Xeon (KVM), Linux x86_64.
rapidgzip parallelises across cores — absolute speedups scale with core count;
treat the figures as directional, not portable.

## Investigation: ZIP “faster than stdlib” and TAR slowdown

### ZIP — yes, caching / methodology artifact

We wrap stdlib `zipfile`, so beating it in steady state is not plausible.

Early harness runs reported ~0.82× because wall timing ran **with measurement
wrappers on** (different ZIP open path: pre-opened `SeekCountingStream` file
obj) and mixed that with log-spam / single-pass noise on ~10 ms samples.

After fixing methodology — **unmeasured** archivey vs stdlib, interleaved
medians, diagnostic logs silenced during the timed window:

| Side | median |
| --- | ---: |
| `zipfile` read-all | ~13.4 ms |
| archivey read-all | ~15.8 ms |
| **ratio** | **~1.18×** |

cProfile shows the same `zlib.decompress` / CRC work on both archivey paths;
we are not winning at decompression — just paying thin wrapper overhead.

### TAR — plain uncompressed, not our codec streams

Harness `tar_read_all` is **raw TAR** (`tarfile` `r:`), not `.tar.gz`. The
slowdown is **not** “we decompress outside tarfile.”

| Case | archivey | peer | ratio |
| --- | ---: | ---: | ---: |
| plain TAR read-all | ~3.6 ms | `tarfile` `r:` ~2.1 ms | **~1.7×** |
| plain TAR list only | ~1.4 ms | `getmembers` ~1.0 ms | ~1.5× |
| already-open member I/O | ~2.0 ms | `extractfile`+read ~1.0 ms | **~2.0×** |
| `.tar.gz` (ad-hoc, two-pass peer\*) | ~21 ms | `tarfile` `r:gz` ~18 ms | ~1.19×\* |

So:

- **Plain TAR** pays per-member archivey overhead (`ArchiveMember`,
  `ArchiveStream`, reader-state). Absolute times are tiny; the ratio looks
  loud because stdlib is ~2 ms.
- **`.tar.gz`**: the ~1.2× above was measured against a peer that called
  `getmembers()` first and so decompressed the stream twice (\*, see the
  accelerator section below). Against a one-pass peer it is about **2.7×**
  (23.2 ms vs 8.5 ms, realistic corpus, 2026-10-20, Linux x86_64 4-core VM,
  CPython 3.11). The gzip single-file row stays near 1.0×, so the cost is in
  the tar-over-codec stack, not in gzip: archivey feeds zlib about 1600 times
  for 16 MiB, roughly 10 KiB of output per call, through the slice and
  read-ahead layers, where `tarfile` makes a few large calls.

## Realistic scale (corrected harness)

**Corpus:** 64×256 KiB ZIP/TAR (~16 MiB), 32 MiB gzip, solid 7z 64×256 KiB.

### Wall-time vs stdlib (fair)

| Case | ratio | ay | stdlib | vs VISION 1.3× |
| --- | ---: | ---: | ---: | --- |
| zip_read_all | **1.18×** | 15.8 ms | 13.4 ms | within |
| tar_read_all | **1.70×** | 3.6 ms | 2.1 ms | above 1.3×, under 2× safety |
| gzip_read_all | **1.02×** | 31.7 ms | 31.2 ms | within |

On a quieter/smaller runner these drift (e.g. zip ~1.18×→~1.55×) — hence wall
ratios are not a PR gate.

### `.tar.gz` / `.tar.bz2` accelerators (off vs on)

Same ~16 MiB multi-member corpus on the **4-core** host above. Stdlib peer is
`tarfile` `r:gz` / `r:bz2` (always stdlib codecs), iterated in one forward pass.
The rows below predate that: the peer then called `getmembers()` first, which
decompresses to the end, and its first `extractfile()` seeked back and decompressed
the stream a second time, so its stdlib times are about twice a one-pass read.

The first nightly that measures after this change will drift-fail the tar.gz and
tar.bz2 rows (accel off and on): their stdlib time roughly halves, so their ratio
roughly doubles. That is the peer changing, not a regression in `src/`, and the new
ratios are the honest ones. Let that night become the baseline, or re-seed with
`skip_drift=true`.
Archivey forces
`use_rapidgzip` / `use_indexed_bzip2` ON or OFF; ON also sets
`MemberStreams.SEEKABLE`.

The bzip2 accelerator is **`rapidgzip.IndexedBzip2File`** (via
`use_indexed_bzip2`), not the separate `indexed_bzip2` package.

| Case | ay | stdlib | vs stdlib | vs accel_off |
| --- | ---: | ---: | ---: | ---: |
| targz accel **off** | 21.3 ms | 18.0 ms | 1.18× | — |
| targz accel **on** | 8.3 ms | 18.2 ms | **0.45×** | **2.57×** faster |
| tarbz2 accel **off** | 375 ms | 366 ms | 1.02× | — |
| tarbz2 accel **on** | 126 ms | 365 ms | **0.35×** | **2.97×** faster |

**Speedup scales with cores** (rapidgzip is parallel). On a 2-core box the same
workload reproduced ~0.88× / ~0.50× vs stdlib — direction holds, absolute
multipliers do not. At **ci** (tiny) scale accel_on is often *slower* than
accel_off (indexing / thread-pool startup dominates).

### 7z BCJ branch filters — liblzma staging vs `pybcj`

`sevenzip_bcj_{lzma2,lzma1,copy}_read_all` were added with the change that moved
branch filters off `pybcj` and onto liblzma
(`dev-docs/investigations/pybcj-upstream-report.md`). liblzma refuses a raw chain whose only filter is a branch filter, so a BCJ stage standing
outside the folder's main chain reframes its input as LZMA2 *uncompressed* chunks;
these cases are what makes that reframing cost visible.

Corpus: 16×256 KiB of x86-like code (one `call rel32` and one `jmp rel32` per
16 bytes, so the filter converts about one branch per eight bytes) = 4 MiB.
Method: the harness's own A/B recipe — `--mode structural --scale realistic`,
sides alternated A,B,B,A,…, per-case minimum over 16 rounds each, sign test over
the per-round pairs, plus a null control (same SHA on both sides, 10 rounds).
Both sides ran the identical harness; only `src/` was swapped.

New = `a50f01a` (liblzma), old = `13ffd75` (`pybcj`). 4-core Intel Xeon (KVM).

| Case | new | old | new/old | rounds new was faster | sign p |
| --- | ---: | ---: | ---: | ---: | ---: |
| `sevenzip_bcj_copy_read_all` | 9.95 ms | 7.64 ms | **1.30×** | 0/16 | 0.000 |
| `sevenzip_bcj_lzma1_read_all` | 38.15 ms | 35.79 ms | **1.07×** | 0/16 | 0.000 |
| `sevenzip_bcj_lzma2_read_all` | 34.94 ms | 34.99 ms | 1.00× | 9/16 | 0.804 |

The null control puts the floor at 1% or better on all three (1.000, 0.995,
0.991; no case worse than 8/10 signs), and none of the other 25 harness cases
shows a consistent sign — so the two significant rows are the change, not the
host.

Reading them:

- **BCJ+Copy is the worst case by construction** — the branch filter is the whole
  decode, so the 1.30× is the reframing cost undiluted: ~420 MiB/s against
  ~550 MiB/s. Nothing else in the suite is that filter-dominated.
- **BCJ+LZMA1 is the shape that actually forces the staging** (the 7-Zip CLI
  writes LZMA1 without an end-of-stream marker, so BCJ cannot join the chain).
  Diluted by LZMA1 decode, the same cost is 1.07×.
- **BCJ+LZMA2 is the control and does not move**: liblzma already ran that whole
  chain itself before the change, `pybcj` was never on that path.

Framing overhead is 3 bytes per 64 KiB of payload (0.005%), so the cost is the
extra pass through liblzma's LZMA2 chunk layer, not the bytes.

### Structural / solid

| Check | Result |
| --- | --- |
| ZIP/TAR/gzip seek counts | within committed baseline bound (silent re-open churn) |
| ZIP/TAR/gzip `bytes == unpacked` | tautological at member-output layer — under-decode guard only |
| solid 7z sequential | decode-once (also pinned in `test_measurement.py`) |
| solid 7z random `read()` | ~32.5× re-decode (n=64) — recorded, not gated |
| solid RAR decode-once | structural gate on committed `basic_solid__.rar` + unit test (`unrar`) |
| encrypted RAR / ZIP AES / ZIP LZMA / in-ZIP accel | structural gate cases (T3); skip when `unrar` / `[crypto]` / `[seekable]` absent |
| solid RAR random | smoke + seek baseline; byte re-decode not observable via unrar pipe (P9) |

ISO / directory: measurement is wired; harness cases are out of scope (ISO
lock baseline lives in `benchmarks/tar_iso_lock_baseline.py`).

### Gate policy

- **PR CI (blocking):** `--mode structural --scale ci` + unit decode-once tests.
- **Change-guarded nightly (off the PR path, `benchmark-wall.yml`):** `--mode full
  --scale realistic` with JSON + markdown report artifact upload (the markdown is also
  written to the Actions job summary so it is readable without downloading) — a daily
  schedule that skips its expensive run unless the default branch changed recently or
  `measured_at` is older than ~30 days; skips re-publish the previous artifact.
  `workflow_dispatch` forces a run (`skip_drift` re-seeds after intentional regressions).
- Wall timing: unmeasured archivey vs stdlib; absolute VISION bands informational;
  nightly hard-fails on wall-ratio *drift* vs the last measured artifact, pass or
  fail.

## Third-party peers: py7zr, rarfile, libarchive-c

The tables above compare against the stdlib only. `benchmarks/peers.py` times
archivey against the other Python readers on the same realistic corpus:

```bash
uv run --extra all python -m benchmarks.peers --scale realistic
```

It is a report, not a gate: none of these rows feeds the nightly drift check.
libarchive-c needs the system `libarchive` (Ubuntu package `libarchive13t64`); without
it, and without `py7zr`, `rarfile` or RARLAB `unrar`, the rows print as skipped.

**Host and versions:** 2026-10-21, `main` @ `8808382`, 4-core Intel Xeon @ 2.80 GHz
(KVM), Linux x86_64, CPython 3.11.17. py7zr 1.1.4, rarfile 4.5 (with `unrar` 7.00),
libarchive-c over libarchive 3.7.2. Three runs of 9 alternating timed calls per side;
each cell is the median of the three per-run medians, and the last column is the
range of the three ratios.

**Read the method notes first:**

- The corpus is the harness corpus. Its payloads repeat a 4 KiB block, so they
  compress far past real data (16 MiB packs into 3.9 KB of 7z, 6.1 KB of RAR,
  93 KB of ZIP). `read_all` and `extract` measure per-member and output handling
  much more than codec speed.
- `list` reads names and metadata only. `read_all` reads every member into memory,
  and checks first that both sides return the same number of bytes. `extract`
  writes to an empty directory. archivey extracts with its default checks (path and
  link safety, atomic writes) and the ratio guard off: these payloads exceed the
  default 1000:1, and the peers have no such guard.
- 7z fixtures: py7zr writes the solid LZMA2 archive (64×256 KiB) and the
  10 000-member solid Copy archive; the 7-Zip CLI writes the 1 000-member non-solid
  store archive (`-ms=off -mx=0`). RAR fixtures are RAR5, written by `rar` 7.00 (solid `-m3` 64×256 KiB;
  10 000 and 1 000 tiny store members).
- Which peer runs `unrar`: archivey and rarfile both send RAR member data through
  RARLAB `unrar`. archivey starts one process for the whole solid archive; rarfile
  starts `unrar p` once per member, and on a solid archive each process decodes
  from the start up to that member. libarchive decodes RAR in C, with no `unrar`.

Ratio is archivey ÷ peer: below 1× archivey is faster.

| Archive | Operation | Peer | archivey | peer | ratio | range |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 7z solid | list | py7zr | 3.8 ms | 1.3 ms | **2.65×** | 2.45–2.89× |
| 7z solid | read_all | py7zr | 47.3 ms | 59.4 ms | **0.87×** | 0.74–0.87× |
| 7z solid | extract | py7zr | 104.4 ms | 85.3 ms | 1.24× | 1.05–1.32× |
| 7z 10 000 members | list | py7zr | 285.3 ms | 194.1 ms | **1.49×** | 1.47–1.54× |
| 7z 1 000 non-solid | list | py7zr | 35.7 ms | 29.9 ms | 1.48× | 1.03–1.69× |
| RAR solid | list | rarfile | 4.2 ms | 1.5 ms | **2.80×** | 2.46–2.83× |
| RAR solid | read_all | rarfile | 75.8 ms | 1497 ms | **0.05×** | 0.05–0.06× |
| RAR solid | extract | rarfile | 150.8 ms | 1568 ms | 0.10× | 0.09–0.10× |
| RAR 10 000 members | list | rarfile | 516.0 ms | 298.3 ms | **1.92×** | 1.70–2.07× |
| RAR 1 000 non-solid | list | rarfile | 38.1 ms | 19.2 ms | 2.02× | 1.96–2.29× |
| ZIP | list | libarchive-c | 3.9 ms | 1.1 ms | 3.58× | 3.57–3.66× |
| ZIP | read_all | libarchive-c | 35.7 ms | 24.9 ms | 1.90× | 1.40–1.90× |
| ZIP | extract | libarchive-c | 81.5 ms | 20.4 ms | 3.92× | 3.59–4.54× |
| TAR | list | libarchive-c | 6.2 ms | 1.4 ms | 4.36× | 4.21–5.06× |
| TAR | read_all | libarchive-c | 22.8 ms | 15.2 ms | 1.48× | 1.34–1.52× |
| TAR | extract | libarchive-c | 78.1 ms | 22.2 ms | 3.51× | 3.40–3.51× |
| 7z solid | list | libarchive-c | 3.0 ms | 0.4 ms | 6.58× | 6.44–7.39× |
| 7z solid | read_all | libarchive-c | 43.3 ms | 35.2 ms | 1.19× | 1.03–1.23× |
| 7z solid | extract | libarchive-c | 116.2 ms | 56.6 ms | 2.07× | 2.03–2.21× |
| 7z 10 000 members | list | libarchive-c | 289.3 ms | 53.9 ms | 5.10× | 4.18–6.55× |
| 7z 1 000 non-solid | list | libarchive-c | 31.6 ms | 4.9 ms | 6.45× | 5.59–6.50× |
| RAR solid | list | libarchive-c | 4.3 ms | 43.6 ms | 0.14× | 0.10–0.15× |
| RAR solid | read_all | libarchive-c | 76.3 ms | 55.9 ms | 1.38× | 1.12–1.77× |
| RAR solid | extract | libarchive-c | 121.3 ms | 58.1 ms | 2.09× | 1.94–2.29× |
| RAR 10 000 members | list | libarchive-c | 481.5 ms | 49.8 ms | 9.67× | 9.42–10.76× |
| RAR 1 000 non-solid | list | libarchive-c | 42.9 ms | 4.6 ms | 8.66× | 8.38–9.92× |

What the numbers show:

- **Listing is not at parity with py7zr and rarfile.** VISION's target is about
  1.25×. Measured: 1.5× on the many-member 7z archives, about 2× on the
  many-member RAR archives, and 2.7–2.8× on the 64-member solid archives (there
  the gap is under 3 ms in absolute terms). A cProfile of the 10 000-member
  archives puts about 40% of 7z listing in `_to_member` (building `ArchiveMember`),
  and about 60% of RAR listing in RAR5 header parsing, with `_to_member` next.
- **Reading data, archivey is at or ahead of the pure-Python peers.** Solid 7z
  `read_all` is 0.87× py7zr. Solid RAR is 20× faster than rarfile to read and
  10× faster to extract, because rarfile decodes a solid archive again for every
  member.
- **libarchive is faster on nearly everything:** 4–10× on listing, 1.2–1.9× on
  `read_all`, 2–4× on `extract`. The exception is listing a solid RAR, where
  libarchive decodes the data to skip it (0.14×).
- **Extraction pays a fixed cost per member** on top of reading: ZIP extract is
  about 2.3× ZIP `read_all` for 64 members. Solid 7z goes from 0.87× py7zr on
  `read_all` to 1.24× on `extract`, so the cost is not specific to ZIP.

## Why the published guide no longer quotes numbers

`docs/access-and-cost.md` used to carry a Measured column from the
`2026-07-23` nightly (`main` @ `89720de`). It was removed: those figures are
the fastest-rotting claim on a published page, and re-running the same harness
at the same `--scale realistic` did not reproduce the ZIP rows.

| Workload | published (`89720de`, nightly) | re-run (`532ae21`, container) |
| --- | ---: | ---: |
| ZIP read-all | 1.87× | **2.57×** |
| ZIP open+list | 4.44× | **5.17×** |
| ZIP extract | 2.38× | **1.41×** |
| gzip read-all | 1.03× | 1.06× |
| tar.gz accel-off\* | 1.27× | 1.27× |
| tar.bz2 accel-off\* | 1.04× | 1.04× |

\* Measured against the two-pass `tarfile` peer (see the accelerator section), so
the absolute values are stale; the comparison between the two columns still holds.

The codec-dominated rows land on the published values, so this is not simply a
slower host — only the ZIP wrapper rows move, and they move in **both**
directions. Consistent with the drift already noted above (zip ~1.18× → ~1.55×
on a quieter runner), the ZIP ratio is dominated by per-member Python overhead
against a stdlib peer measured in milliseconds, which is exactly the shape that
does not survive a change of machine.

The guide now states the aspirational bands and the re-run command, and points
here for figures with a host and a commit attached. Absolute numbers belong
where their provenance travels with them.
