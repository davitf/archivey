# Known issues

This page lists only live archivey defects and live upstream bugs that archivey works
around. Each entry is short: symptom, affected versions, what archivey does, what remains,
upstream status, and a link to the evidence. A fixed archivey defect is deleted in the PR
that fixes it; the regression test and git history keep the record. Measurements, root
causes and rejected fixes live in [`investigations/`](investigations/), the standing rule a
mitigation imposes lives on the format handbook page ([`formats/`](formats/)), and by-design
behaviour lives in the handbook's sharp-edges tables and, when users hit it, in
[`docs/gotchas.md`](../docs/gotchas.md).

## MacPaw `unar` / XADMaster: RAR5 solid after an empty entry is silent-wrong (open)

**Symptom.** On a RAR5 solid archive, `unar` fails on a member with data that comes after
an empty file or a directory, on stdout and on extract-to-disk. Selecting only the later
member does not help. Debian's 1.10.1 SIGSEGVs and loses output it had buffered for
earlier members; the XADMaster 1.10.8 lineage (the local build and Homebrew's bottle)
exits 0 with empty output. RAR4 is unaffected.

**What archivey does.** `unar` is the second RAR data program (used when no RARLAB program
is found, or with `ArchiveyConfig.rar_decompressor="unar"`). `internal/backends/rar_unar.py`
refuses, from the native listing, every RAR5 solid member with data that follows an empty
file, a directory or a link, and names only the readable members to `unar`, so it never
reaches the crash. The same module handles the six other `unar` failures found on the
fixtures: it refuses RAR 1.5 compression, encrypted RAR 2.x to 4.x data, non-ASCII
passwords, header-encrypted RAR5 volume sets and prefixed volume sets, copies a prefixed
single archive from where the RAR starts, and names stream volumes under the set's own
scheme. Pinned by `tests/test_rar_unar.py`, which CI's macOS leg runs against the
Homebrew bottle.

**What remains.** The gate is built from measured shapes, and ANTI members and
packed-nonzero empty entries are untested, so a shape could slip past it. The per-member size and digest check
is the net.

**Upstream.** Not filed with XADMaster.

**Evidence.** [`alternative-rar-decompressors.md`](investigations/alternative-rar-decompressors.md)
§2026-09-26 measurements. Handbook: [`formats/rar.md`](formats/rar.md) §3.

## Debian/Ubuntu `unar` 1.10.1: some compressed RAR5 members come out empty (open)

**Symptom.** `unar` 1.10.1 (Debian and Ubuntu's package) writes nothing for a compressed
RAR5 member when a Huffman lookup near its end peeks past the last packed byte, solid or
not, and exits 0; under `-o -` stderr is empty too. About 1–6% of `-m3` members measured
do this. In a solid run over several entries, later members are lost as well, or come
out as stale window bytes, so the bytes found where the dropped member should be belong
to something else. Debian's 1.10.8 packages before 1.10.8+ds1-10 do the same (see Cause).

**What archivey does.** `find_unar` runs each `unar` it finds once, after the banner
check, on an 85-byte RAR5 archive embedded in `internal/external/unar.py` (the
`unar_drop__.rar` fixture), and requires the member's exact 14 bytes back. A build that
writes anything else, exits non-zero, or runs out of time is not used, and the answer
is cached per binary like the banner's. Under `"auto"` that `unar` counts as absent;
with `rar_decompressor="unar"` a read raises `PackageNotInstalledError`. For the
patch's signature (exit 0, less than the member) it names the patch and says to install
RARLAB `unrar` or a `unar` without the patch; any other failure says what the run did
(its exit status, a signal, the wrong bytes). A check that cannot run at all, such as a
temporary directory with no space, is reported as that and is not cached.
`unar_rar5_probe_failure(path)` runs the check on any binary. Before the check existed,
and still as a second line for a build that passes it: every member read with `unar` is
checked against its declared size and its stored CRC32 or BLAKE2sp, so a dropped member
is reported as truncated and misplaced bytes in a solid run fail the digest; the solid
pass reads a member that has no digest through a `unar` run of its own, which writes it
exactly or not at all. Pinned by `tests/test_unar_probe.py`, and by
`tests/test_rar_unar.py` on the `unar_drop*` and `unar_stale*` fixtures.

**CI.** The Linux test legs install Ubuntu's `unar`, build upstream XADMaster 1.10.8
from source (`scripts/install-unar-from-source.sh`) and put it first on `PATH`, so the
`unar` tests run against a build archivey uses. A separate step *expects* Ubuntu's
`/usr/bin/unar` to fail the check
(`test_distro_unar_matches_the_ci_expectation`, with `ARCHIVEY_DISTRO_UNAR_EXPECT=patched`).
The `unar-distro-packages` job does the same in containers for `debian:stable`
(expected patched), `debian:unstable` (expected clean) and `fedora:latest` (expected
clean); Alpine does not package `unar`. When one of these fails, the package changed:
flip the row's expectation, and, if Ubuntu's package passes, consider dropping the source
build. The test's failure message and the workflow comments say the same.

**What remains.** With a patched build and no other program, RAR member data cannot be
read with `unar` at all. A local dev box with Ubuntu's package skips the `unar` tests
unless a 1.10.8 build comes first on `PATH`; `scripts/setup-dev-env.sh` reports
`REFUSED unar` when that is the case.

**Cause.** Not upstream: it comes from `CSInputBuffer-bit-string-reading.patch`, which
Debian added in 1.10.1-2 and deleted in 1.10.8+ds1-10 (June 2026, for breaking imploded
ZIP members, Debian bug #1134346). With it, the bit reader raises end of file when fewer
bits remain than a Huffman lookup *peeks*, even when the code it uses is shorter; the
error is swallowed and the member comes out empty. `scripts/find_unar_probe_member.py`
models those reads and found the probe member; its `--validate 300 --check-unar
/usr/bin/unar` agreed with Ubuntu 24.04's package on all 563 compressed cases, 42 of
them solid pairs decided by the first member (2026-10-02). Measured on builds from source
(2026-10-01): upstream 1.10.1, 1.10.7, 1.10.8 (Homebrew's version) and master decode all
300 generated RAR5 archives and the `unar_drop*` / `unar_stale*` fixtures correctly; the
Debian 1.10.1 package build reproduces the drops, the same build without that patch does
not, and upstream 1.10.8 with the patch applied drops the probe member too. The RAR 1.5
refusal (`rar15-comment.rar` missing `FILE1.TXT`) has the same cause.

Which packages carry it (patch series read from the source packages in Ubuntu's archive,
2026-10-01): Ubuntu 22.04 (1.10.1-2build11), 24.04 (1.10.7+ds1+really1.10.1-3build1) and
26.04 (1.10.8+ds1-9build1) do; Debian 1.10.8+ds1-9 does, and is expected to be what
Debian 13 ships (not confirmed: no Debian mirror was reachable); Debian unstable's
1.10.8+ds1-10 does not. So no version string identifies an affected build: Debian's
1.10.8 packages print `unar v1.10.8` with or without the patch, and upstream 1.10.8 and
master still print `v1.10.7`. That is why archivey checks behaviour, not the banner.

**Upstream.** Nothing to file with XADMaster; the patch is Debian's, and Debian has
dropped it. Ubuntu picks that up with its next sync from Debian.

**Evidence.** [`alternative-rar-decompressors.md`](investigations/alternative-rar-decompressors.md)
§Compressed RAR5 member dropped. Handbook: [`formats/rar.md`](formats/rar.md) §3.

## stdlib `tarfile` treats a corrupt non-first header as clean end-of-archive (open)

**Symptom.** `tarfile.TarFile.next()` re-raises `InvalidHeaderError` only at offset 0. A
corrupt member header anywhere later is swallowed and iteration ends, so mid-archive
corruption gives a shortened listing with no error. All supported Python versions.

**What archivey does.** `TarReader._verify_tar_eof` classifies the block the walk stopped
on and raises `CorruptionError` for a rejected header, in random access even when it is
the archive's last block.

**What remains.** In streaming mode tarfile's `_Stream` hides its header reads, so a
rejected header that is the archive's final block reads as a missing trailer and surfaces
as the `ARCHIVE_EOF_MARKER_MISSING` warning, not `CorruptionError`. A native TAR header
walker ([`formats/tar.md`](formats/tar.md) §7) would close it. Users are told in `docs/formats.md` and
`docs/gotchas.md`.

**Upstream.** CPython behaviour; not filed.

**Evidence.** [`formats/tar.md`](formats/tar.md) §2.2, §5 and §7.

## A TAR member's seek past its end returns the member size, not the target (open)

**Symptom.** With `seekable_members=True`, `seek(10)` on a 3-byte TAR member returns 3 and
leaves `tell()` at 3, where `io.BytesIO` and a real file return 10. Reads agree either way
(both return `b""`).

**What archivey does.** Nothing; the stream is stdlib `tarfile`'s `ExFileObject`, which
clamps the position to the member size.

**What remains.** Code that checks `seek()`'s return value sees a different position from
other backends.

**Upstream.** CPython behaviour; not filed.

**Evidence.** [`formats/tar.md`](formats/tar.md) §5; `docs/access-and-cost.md`.

## rapidgzip accelerator: upstream defects

Bugs 3, 4 and 5 below are live in rapidgzip 0.16.0, the current and floor version. Bugs 1 and
2 (closing accelerator objects at finalization, one accelerator library per process) are
fixed on archivey's side and written up in
[`rapidgzip-upstream-report.md`](investigations/rapidgzip-upstream-report.md) §6 and §7.

### Bug 3: rapidgzip terminates the process when its Python source raises (open)

**Symptom.** When a rapidgzip object decodes from a Python file object and a callback into
that object raises (for example, the stream was closed underneath it), the C++ layer throws
`std::invalid_argument` ("Cannot convert nullptr Python object to the requested result
type") through a `terminate()` boundary and the process aborts with SIGABRT. It fires on
`read()`, on `close()` and on the GC-time finalize guard, so no Python `try` contains it.
Path sources are unaffected, because rapidgzip owns its own handle.

**What archivey does.** It never closes a source underneath a live accelerator stream: the
single-file reader's `_close_archive` leaves the non-owning `SharedSource` behind member
streams open, so `reader.close()` with a member stream still open cannot trigger it. For a
caller's own stream that fails or is closed mid-read:

- Every codec rapidgzip decodes (gzip, zlib, raw DEFLATE and bzip2) runs in a child
  process (Bug 4). The child's source never raises (a failed read is an end of input);
  this process serves the reads from the caller's stream and raises the caller's
  exception itself, marked as the caller's, so an `EOFError` from a dropped network
  stream stays an `EOFError`. The decoder took the fault for the end of its input, so the
  stream is then given up for good: every later `read`, `seek` and `tell()` raises
  `ReadError`, even after the caller's source recovers (`compressed-streams`, the
  accelerated-decoder paragraph).

Pinned by `tests/test_accelerator_bug3_trap.py`. The stdlib codec fallbacks raise an
ordinary `ValueError`.

**What remains.** The child process is needed until rapidgzip stops terminating.

**Upstream.** Not filed.

**Evidence.** `tests/test_accelerator_bug3_trap.py`; [`formats/bzip2.md`](formats/bzip2.md)
§2.3.

### Bug 4: rapidgzip aborts on a truncated DEFLATE stream (open, contained)

**Symptom.** rapidgzip calls `std::terminate` (SIGABRT) when it decodes a gzip, zlib or raw
DEFLATE stream that ends early:

```
terminate called after throwing an instance of 'std::logic_error'
  what():  The bit buffer should not contain more data than have been read from the file!
```

The throw comes from a destructor (`GzipChunk::determineUsedWindowSymbolsForLastSubchunk`
→ `BitReader::tell()`), so it fires for a path, a file object and a `BytesIO` alike, on
files from about 380 KB up; on an 8 MB gzip, 27 of 30 random cuts aborted. The macOS build
raises `Unexpected end of file when getting block ...` instead. bzip2
(`IndexedBzip2File`) never aborted in 110 tries, nor in the crash search below.

**What archivey does.** gzip, zlib and raw DEFLATE decode through rapidgzip in a child
process (`rapidgzip_child.py` running `rapidgzip_worker.py`). bzip2 does too, as a
precaution: its decoder has not been seen to abort, but it comes from the same library.
The abort ends the child, and
the parent reports it by how the child ended: an abort naming this truncation is
`TruncatedError`, another crash `CorruptionError`, SIGKILL `ResourceLimitError`, anything
else `ReadError`. `tests/test_accelerator_truncation_abort.py` pins the reporting and
keeps a canary that raw rapidgzip still aborts on Linux; when the canary fails, rapidgzip
may be safe in-process again.

A crash hands the read to the standard library, which starts at the last index point the
reader passed, so a cut stream delivers the same bytes as without rapidgzip
([`formats/gzip.md`](formats/gzip.md) §2.3). Before that, rapidgzip's read-ahead lost
everything it had decoded past the reader: up to the whole stream for a cut file of tens
of MB with every core decoding.

**What remains.** Nothing runs rapidgzip in the caller's process. To know whether the
isolation is still needed, `scripts/accelerator_crash_search.py` runs both decoders
in-process on damaged input, in a process it watches, and reports each crash by its
signature; `.github/workflows/accelerator-crash-search.yml` runs it weekly and on a
lockfile change, on Linux, macOS and Windows. A new signature (any bzip2 crash) turns the
run red; a run with cut inputs that no longer finds this abort suggests it is fixed.

**Upstream.** Not filed. It is the one worth filing: a destructor must not throw, and the
input is only short, not hostile. The throw is the `Finally` guard's `bitReader.seekTo()`
in `GzipChunk::determineUsedWindowSymbolsForLastSubchunk`; wrapping that call in
`try`/`catch` and building 0.16.0 from source ended the aborts, and a cut stream then
raised a catchable `RuntimeError` after every chunk before the cut.

**Evidence.** [`rapidgzip-upstream-report.md`](investigations/rapidgzip-upstream-report.md)
§2; the design and costs are in [`formats/gzip.md`](formats/gzip.md) §2.3.

### Bug 5: rapidgzip keeps the Python file object it reads from (open, contained)

**Symptom.** `IndexedBzip2File(f)` never releases `f`: after `close()` and a garbage
collection, a weak reference to `f` is still alive, and so is every bound method of `f` it
took. Each open also leaks a little native memory. Measured on rapidgzip 0.16.0 with an
`io.BytesIO` subclass, through archivey and directly.

**What archivey does.** bzip2 decodes in a child process (Bug 4), one per stream, and the
child's memory goes when it ends. The child reads a caller-owned stream through this
process, which drops it when the stream closes. Before bzip2 moved to the child, an
in-process shim between rapidgzip and the caller's stream did that, and before the shim,
every accelerated bzip2 open of a `BytesIO` leaked a full copy of its buffer: a fuzz run
over the accelerated bzip2 path ran out of memory after about 36 000 inputs.

**What remains.** Nothing in the caller's process.

**Upstream.** Not filed. The report, with a ten-line reproduction, is ready in
[`investigations/rapidgzip-upstream-report.md`](investigations/rapidgzip-upstream-report.md) §9.

**Evidence.**
`tests/test_accelerator_corruption.py::test_indexed_bzip2_frees_a_stream_source_after_close`
pins that the caller's stream is freed. In-process, Python objects measured with `tracemalloc` over 1 000 opens after
200 warm-up opens of a 4 kB `.bz2`: 0 bytes per open from a path, about 1.7 kB from an
`io.BytesIO`. Native memory measured by RSS over 3 000 opens: 2 to 5 kB per open, noisy,
for every source.

## Intermittent `pyppmd` native aborts on PPMd streams (open upstream)

**Symptom.** pyppmd 1.3.x corrupts the heap (SIGSEGV or SIGABRT on Linux,
`STATUS_HEAP_CORRUPTION` 0xC0000374 on Windows) when a decode asks for more output than the
stream holds: `max_length=-1`, a sized request about 64 KiB or more past the true
remaining output, or a `decode` after `eof`. Valgrind pins the first bad write as a
use-after-free of pyppmd's own output buffer at `ThreadDecoder.c:134`. Random input (a
wrong 7z AES key, a hostile archive) reaches a second route: a model that ended early and
is fed more input segfaults in `Ppmd7_DecodeSymbol`, on 1.2.0 as well. PPMd8 behaves the
same way.

**Affected versions.** The overshoot crash is a regression from 1.3.0's `ThreadDecoder.c`
rewrite (upstream PR #126): 1.1.1 and 1.2.0 do not crash on it but return wrong bytes on
chunked decodes, which is why `[recommended]` requires `pyppmd>=1.3.1`. The random-input
crash affects 1.2.0 and 1.3.x.

**What archivey does.** `PpmdDecoder` (`streams/codecs/ppmd_decoder.py`) bounds every request by
the exact remaining `unpack_size` and never passes `-1`; refuses unsized PPMd7 at
construction; decodes unsized PPMd8 in bounded 64 KiB requests; injects at most one capped
NUL at the end; stops at a spent payload; and hands pyppmd the whole member in one
`decode`, so a short return is the end. Members over
`DecoderLimits.max_ppmd_in_process_input` (16 MiB by default) decode in a child process
(`ppmd_child.py`), where a crash becomes `CorruptionError` (`ResourceLimitError` for
SIGKILL or a refused `mem_size`, `ReadError` for other deaths). Pinned by
`tests/test_ppmd_crash_isolation.py` and `tests/test_ppmd_raw_streams.py`; the
non-blocking PPMd native stress workflow watches for regressions.

**What remains.** A crafted 7z or ZIP header that inflates `unpack_size` 64 KiB or more
past the member's true content puts the one in-process decode call into the crashing class
(for members under the in-process limit). Archivey cannot detect the lie before decoding;
the CRC check catches the garbage output after the fact. Only an upstream fix closes it.

**Upstream.** Not filed. The ready-to-file report, its reproduction scripts and the
verification checklist for a fixed release are
[`ppmd-native-investigation-results.md`](investigations/ppmd-native-investigation-results.md)
§J.

**Evidence.** Same file: root cause §D, archivey's options §I, CI fingerprints, version
matrix, crash-shape rates and the stress workflow §K.

## `pyppmd` exit-after-green abort (`test_ppmd_raw_streams` teardown)

**Symptom.** `tests/test_ppmd_raw_streams.py`, run in its own pytest process, can pass
every test and then die in interpreter teardown or GC with SIGSEGV or
`corrupted size vs. prev_size`. Same pyppmd 1.3.x defect as above, reached at teardown:
`Ppmd7T_Free` wakes a worker still blocked on input, and it writes into the freed output
block.

**What archivey does.** `PpmdDecoder._quiesce_worker`, called from `close()` and
`__del__`, drives a parked worker to `finished` with bounded `decode(b"\0", 1)` before the
decoder is freed. `scripts/ppmd_uaf_valgrind.py` reports 0 memcheck errors on archivey's
scenarios with it. Adversarial unfinished-decoder tests run in subprocess children.

**What remains.**

1. The teardown race. Quiescing removes the use-after-free on the shapes valgrind
   reproduces, but it is defence in depth, not proven elimination: the race is timing and
   platform dependent (hotter on 3.12+, free-threaded builds and Windows). Required CI
   keeps `--allow-exit-after-green` for this module until the valgrind gate runs green on
   those platforms.
2. A sized pack declared complete but internally corrupt can still fill toward
   `unpack_size` through empty drains; the container CRC is the backstop.
3. `pack_size` must measure the same bytes `feed()` counts (the invariant in the
   `PpmdDecoder` docstring); the 7z pipeline satisfies it by construction.

**Upstream.** Part of the same unfiled report (§J above).

**Evidence.** [`ppmd-exit-after-green-exploration.md`](investigations/ppmd-exit-after-green-exploration.md);
[`ppmd-native-investigation-results.md`](investigations/ppmd-native-investigation-results.md)
§D, §I and §K.5.

## Intermittent Linux full-suite heap corruption (`[all]` / Hypothesis late crash)

**Symptom.** On GitHub Actions `ubuntu-latest` required legs with `--extra all` or
`all-lowest` (py3.11 to 3.13, sometimes 3.14), the suite process dies with
`Fatal Python error: Segmentation fault` (exit 139) or `Fatal Python error: Aborted` (exit
134). The stack at death is a late symptom of a heap already corrupt, not the corrupting
call:

| Crash site (examples) | What it means |
|-----------------------|---------------|
| `Garbage-collecting` → `hypothesis/internal/charmap.py` during `test_property_safety.py::test_normalize_total_and_idempotent` | Hypothesis touches the allocator after earlier native work poisoned the heap |
| `Garbage-collecting` → `subprocess._close_pipe_fds` / `Popen` in `rar_unrar.open_unrar_p` (e.g. `test_multi_volume_stream_materialization`) | GC plus a new subprocess while the heap is bad |

Fatal logs list `backports.zstd`, `lz4`, `_brotli`, `pyppmd.c._ppmd`, `rapidgzip` and
`_cffi_backend` among the loaded extension modules. `[core-only]` legs and the macOS and
Windows `[all]` legs are typically clean on the same commits. A local
`uv run --no-sync pytest tests/ -q` is often green, so there is no one-shot repro. This is
not the pyppmd defect above: a green PPMd stress run does not clear it, and the fatal
stacks are not in a PPMd decode.

**Suspects (unconfirmed).**

1. The in-process rapidgzip decoder in a long-lived pytest process. Every codec now runs
   rapidgzip in a child process (Bug 4), bzip2 included, so the suite's own process no
   longer loads it. The last recorded red runs predate the move and the rate has not been
   re-measured since.
2. Allocator layout: many natives loaded together, plus coverage and GC, with Hypothesis
   or `subprocess` only the tripwire.
3. Not the gzip/zlib truncation-recovery logic: the crashes do not stack in
   `DecompressorStream` or `verify.py`.

**What archivey does.** CI splits the required `[all]` and `[all-lowest]` jobs into four
steps so a corrupted heap in one cannot take down another. That is CI hygiene, not a
product fix.

**What remains.** The cause. The next step is a soak rate, then an A/B run without
rapidgzip.

**Upstream.** Unknown owner; nothing to file yet.

**Evidence.** [`linux-heap-corruption-soak.md`](investigations/linux-heap-corruption-soak.md):
the CI step split, the soak and A/B recipes, and the recorded red runs.
