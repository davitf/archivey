# Known issues

> See also [Compression-library analysis](library-analysis.md) for which library backs each
> codec and why — including why `rapidgzip` is the single accelerator library (the issue below)
> and why an `indexed_zstd` zstd accelerator would face the same constraint.

## 7z SFX scan: a CRC-valid inexact decoy beat a later real payload (fixed)

**Status: fixed.** The SFX scan used to return the earliest `HitOutcome.VALID` 7z
needle. A 32-byte signature header in the stub, with a matching `StartHeaderCRC` and a
nonzero declared end inside the remaining source, won over the real archive appended
after it, and `open_archive` failed on the decoy's header.

The 7z validator now grades a hit whose declared end stops short of the known source end
`VALID_SHORT`. Both scans (detection's and the forced-format one in
`internal/sfx.scan_for_magic`) keep the first such hit as a fallback and keep looking for
a hit that ends exactly at EOF; only when none does is the short one the answer. Trailing
bytes after a real payload (SFX configuration, an appended signature) therefore still
open. A decoy that declares its end exactly at EOF is still accepted: that is a
constructed polyglot, and `format=` with `start_offset` is the escape. Pinned by
`test_inexact_7z_decoy_loses_to_a_later_exact_payload` in `tests/test_sfx.py`.

## 7z BCJ branch filters: two `pybcj` defects, now decoded through liblzma (fixed)

**Status: fixed in archivey; both defects open upstream.** Found in the S15 7z sweep as
finding K5. Two ordinary archives — written by 7-Zip, verified by `7z t`, read back
correctly by 7-Zip itself — were unreadable, because archivey ran BCJ branch filters
through `pybcj`. They now run through liblzma, in core, for every folder shape, and
`pybcj` has left the `[recommended]` extra. Kept here because the measurements are what
rule out the obvious cheaper fixes, and because the upstream bugs are still live for
anyone else calling `pybcj` — py7zr included.

### Defect 1: a member of 2 GiB or more could not be opened at all

`pybcj`'s C extension parses the decoder's stream-size argument as a C **signed** `int`:

```
>>> import bcj
>>> bcj.BCJDecoder(2147483647)      # 2 GiB - 1
<bcj.BCJDecoder object at ...>
>>> bcj.BCJDecoder(2147483648)      # 2 GiB
OverflowError: signed integer is greater than maximum
```

Archivey passed the folder's declared unpack size straight in, so `reader.open(member)`
raised that builtin from `sevenzip_pipeline.open_folder_pipeline` → `_execute_stage`,
before a byte was read. Listing the same archive worked. All six decoders took the same
argument and failed the same way: `BCJDecoder` (x86), `ARMDecoder`, `ARMTDecoder`,
`PPCDecoder`, `SparcDecoder`, `IA64Decoder`. Sizes at or above 2^63 fail earlier still,
with `Python int too large to convert to C long` — the argument is read as a C `long` and
then range-checked to `int`.

Nothing is crafted and no attacker is involved: `7z a -m0=BCJ -m1=LZMA` over any
2 GiB-plus file produces one. (A crafted archive reaches it more cheaply — a header may
declare a multi-GiB unpack size behind a few hundred bytes of pack data — but the bug
needs no crafting.)

**`OverflowError` is not an `ArchiveyError`.** `SevenZipReader._translate_exception` maps
only `EOFError`, and the base translator returns `None` by design — CONTRIBUTING's
never-a-catch-all rule, correct for *unrecognized* exceptions. This one was reachable on a
**valid** archive, so `except ArchiveyError` missed a failure
`docs/errors-and-diagnostics.md` promises it catches. That was archivey's own bug whatever
upstream decided, and the fix removes it rather than translating it.

### Defect 2: IA64 lost the trailing partial block, at any size

`pybcj`'s IA64 decoder drops the final incomplete 16-byte block. A 2911-byte member came
back as 2896 bytes, so archivey raised `TruncatedError` on an archive `7z x` extracts
byte-for-byte. It reproduces from **21 bytes** up — no size threshold, no crafted input.
The other five filters are unaffected: over 120 random payloads each, liblzma and `pybcj`
agreed exactly for x86, ARM, ARMT, PPC and SPARC, and disagreed on IA64 in 80 of 120.

### Which archives were affected

Measured by spying on `bcj.BCJDecoder` while archivey read a small archive of each shape,
so the rows say where a `pybcj` stage was built at all, not just where 2 GiB was reached:

| Folder coders | pybcj stage built with | Affected |
| --- | --- | --- |
| `BCJ` + `LZMA` (LZMA1) | the member's unpack size | yes |
| `BCJ` + `PPMd` | the member's unpack size | yes |
| `BCJ` + `BZip2` | the member's unpack size | yes |
| `BCJ` + `Deflate` | the member's unpack size | yes |
| `BCJ` + `Copy` | the member's unpack size | yes |
| `BCJ` + `LZMA2` | *(none built)* | **no** |

`BCJ` + `LZMA2` was exempt because `sevenzip_pipeline._plan_lzma_family` folds the branch
filter into one liblzma chain there (`[FILTER_X86, FILTER_LZMA2]`) and never reached
`pybcj`. Confirmed at full size, not only structurally: the same 2.1 GiB payload written
with `7z a -m0=BCJ -m1=LZMA2` read back through archivey in 28 s to a SHA-256 matching the
source. The other pairs staged BCJ separately — LZMA1 because of the BPO-21872 truncation
the module documents, the rest because liblzma will not run a raw chain whose only filter
is a BCJ (`lzma.LZMADecompressor(FORMAT_RAW, [{"id": FILTER_X86}])` raises
`LZMAError: Invalid or unsupported options`).

### py7zr fails identically (measured, not inferred)

py7zr 1.1.3 lists the 2 GiB archive and then raises the same exception from
`compressor.py:671` → `_get_alternative_decompressor` → `BCJDecoder.__init__` →
`bcj.BCJDecoder(size)` (`compressor.py:467`), passing `unpacksizes[i]` with no bound. The
ceiling is `pybcj`'s API surfacing in every caller, not an archivey-specific mistake.

Its staging is the same shape as archivey's: a BCJ coder beside LZMA2 is folded into the
liblzma chain, and beside anything else it is handed to `pybcj` instead (the "hack for
LZMA1+BCJ which should be native+alternative" at `compressor.py:634`). **But its three
branch-filter lists — `compressor.py:620`, `:767` and `:836` — name x86, ARM, ARMT, PPC
and SPARC and omit IA64**, so LZMA1+IA64 is the one combination py7zr still sends through
a single combined chain. Measured 2026-09-19 on 2911 bytes written by `7z a -m0=IA64
-m1=LZMA`: the five listed filters extract correctly, and IA64 **hangs** — the truncated
look-ahead never arrives, `out_remaining` never reaches zero, and `py7zr.py:1507`'s
`while out_remaining > 0` spins at 100% CPU with no error and no timeout. archivey reads
all six correctly. Drafted, not filed:
[`investigations/py7zr-upstream-report.md`](investigations/py7zr-upstream-report.md).

### What archivey does now

A BCJ coder inside an LZMA2 chain is folded into that chain, as before. A BCJ coder staged
on its own — after LZMA1, after a non-LZMA codec, or alone — runs as a raw liblzma chain of
`[<branch filter>, FILTER_LZMA2]` with its input framed as LZMA2 **uncompressed** chunks
(`_Lzma2Framer` in `streams/decompress.py`). The framing exists only because liblzma
rejects a chain whose last filter is not a compression filter; it compresses nothing and
costs 3 bytes per 64 KiB, 0.005% of the payload. The declared unpack size no longer reaches
the filter at all — it decides only whether the stream finished.

Measured on the 2.1 GiB BCJ+LZMA1 archive: 2 254 857 830 bytes in 30.7 s, SHA-256 matching
the source, against 28.0 s for the same payload as BCJ+LZMA2. LZMA1+BCJ still never goes
into one combined `FORMAT_RAW` chain; BPO-21872 is a separate defect and that staging rule
is unchanged.

### The cheaper fixes that look right and are not

Both were tested rather than reasoned about, because both are the first thing to try.

**Clamping the declared size** does not work: the size is not a buffer hint but the
position at which `pybcj` releases its final look-ahead bytes. Over 300 random payloads
decoded against liblzma's `FILTER_X86` as the reference, a declared size **equal** to the
true size was correct 300/300; half the true size was wrong 232/300, `1` was wrong 257/300,
and an overstated size was wrong **300/300** — output either 4 bytes short or the same
length with wrong bytes. That is the silent truncation CONTRIBUTING forbids.

**Chunking `pybcj` per 2 GiB window** does not work either: the x86 filter is
position-dependent (`distance = current_position + i`) and `pybcj` exposes no start offset,
so a fresh decoder for a window beginning at a nonzero offset decodes wrong. A 64 KiB x86
payload split into 16 KiB windows came back with 9 318 of 65 536 bytes wrong.

**`pybcj`'s own pure-Python fallback (`bcj._bcjfilter`)** accepts a 3 GiB stream size, but
runs at 3.2 MiB/s against the C extension's 582 MiB/s on the same x86-like data (182x
slower; roughly eleven minutes for a 2.1 GiB member), and is **not** equivalent: on a
64 KiB x86-like input its output differs from liblzma's in the final two bytes — report 2
in [`investigations/pybcj-upstream-report.md`](investigations/pybcj-upstream-report.md),
which carries all three pybcj drafts.

### Reproduction

Needs ~2.5 GB of free disk and a few minutes of CPU. The IA64 case needs neither.

```bash
python - <<'PY'
pat = bytes([0x8B, 0x45, 0xF8, 0xE8, 0x10, 0x20, 0x00, 0x00,
             0x89, 0x45, 0xFC, 0xE9, 0x00, 0x01, 0x00, 0x00])
chunk = (pat * (1 << 16))[: 1 << 20]
total = int(2.1 * (1 << 30))          # 2 254 857 830 bytes
with open("big.bin", "wb") as f:
    written = 0
    while written < total:
        n = min(len(chunk), total - written)
        f.write(chunk[:n])
        written += n
PY
7z a -m0=BCJ -m1=LZMA big_bcj_lzma1.7z big.bin
7z t big_bcj_lzma1.7z                            # Everything is Ok

head -c 2911 big.bin > ia64.bin
7z a -m0=IA64 -m1=LZMA ia64.7z ia64.bin
```

Both read correctly now. Against the pre-fix reader, the first raised
`OverflowError: signed integer is greater than maximum` from `reader.open(member)` and the
second raised `TruncatedError`. The upstream defects are still reproducible directly:

```python
import bcj

bcj.BCJDecoder(2**31)                     # OverflowError

pat = bytes([0x8B, 0x45, 0xF8, 0xE8, 0x10, 0x20, 0x00, 0x00,
             0x89, 0x45, 0xFC, 0xE9, 0x00, 0x01, 0x00, 0x00])
src = (pat * 20)[:21]
encoder = bcj.IA64Encoder()
filtered = encoder.encode(src) + encoder.flush()
assert bcj.IA64Decoder(len(src)).decode(filtered) == src   # fails: 16 bytes of 21
```

| | |
| --- | --- |
| Payload | 2 254 857 830 bytes (2.1 GiB) of repeating x86-like code |
| Archive | 93 760 017 bytes, `Method = BCJ LZMA:24`, one folder, `7z t` Everything is Ok |
| LZMA2 control | same payload, `Method = BCJ LZMA2:24`, 93 760 614 bytes |
| Measured | CPython 3.11.15, pybcj 1.0.7, py7zr 1.1.3, 7-Zip 23.01, Linux x86-64 |

The regression tests are in `tests/test_sevenzip_reader.py`: the IA64 case round-trips a
2911-byte member through `7z`, and the 2 GiB case pins `FilterDecoder` against an `unpack_size`
of 2^31 without building a fixture, since the size no longer reaches the filter.

## MacPaw `unar` / XADMaster: RAR5 solid after an empty entry is silent-wrong (open)

**Status:** open upstream; worked around. `unar` is the second RAR data program (used
when no RARLAB program is found, or with `ArchiveyConfig.rar_decompressor="unar"`),
and `internal/backends/rar_unar.py` refuses every read below before `unar` runs.
Evidence:
[`alternative-rar-decompressors.md`](investigations/alternative-rar-decompressors.md).

On a **RAR5 solid** archive, `unar` fails on a member with data that comes **after** an
empty file or a directory — stdout **and** extract-to-disk. Selecting only the later
member does not help: the decoder still walks the earlier slot. An empty entry that comes
last is harmless, and so is every member before the first empty entry.

| `unar` lineage | `unar -o -` / disk extract |
| --- | --- |
| Debian `unar 1.10.7+ds1+really1.10.1` | **SIGSEGV**. Output of *earlier* members that `unar` had buffered is lost too (`wildcard_names_solid__.rar`: 8192 of 8202 bytes of member 0) |
| Locally built MacPaw XADMaster `v1.10.8` (banner **v1.10.7**) | **rc=0**, empty output (silent wrong data) |
| homebrew-core formula `unar` (XADMaster **v1.10.8**) | Same 1.10.8 lineage. CI's macOS leg installs it and runs `tests/test_rar_unar.py` against it |

Measured 2026-09-26 with `rar a -s -ds -m3` probes (order kept): empty file first or in
the middle fails, empty file last passes, a directory between members fails
(`wildcard_names_solid__.rar`), directories written last pass. RAR4 passed every shape.

**The gate as shipped.** From the native listing: in a RAR5 solid archive, refuse every
member with data that follows an empty file, a directory, or a link (links are included
without a failing sample, on the same no-data-in-the-stream grounds). The solid pass then
names only the readable members by index, so `unar` never reaches the crash and loses no
buffered output. RAR4 is not gated; the size and digest check on each member is the net.

**Six more `unar` behaviours found on the committed fixtures**, all handled:

- **RAR 1.5 compression** (`rar15-comment.rar`, `FILE1.TXT`, method 3 at version 15):
  `unar` writes nothing for the member and exits 0. Refused when a member's extract
  version is below 20 and it is not stored. The same archive's comment blobs carry the
  same method (`extract_version=15`, `compress_type=0x34`), and `unar` 1.10.1 decodes
  them correctly through the one-file RAR `decompress_rar3_blob` builds (measured
  2026-09-26, archive and both member comments). Comments are therefore not gated; the
  stored CRC16 drops any wrong or missing output under either program.
- **A prefix before the RAR** (an SFX stub, or any leading bytes): `unar` reports an
  unknown format. A single prefixed archive is copied from where the RAR starts; a
  prefixed multi-volume set is refused.
- **Volume names follow the header** (`tinyvol_rnn.rar` + `.r00` given as streams):
  `unar` looks for volume 2 only under the scheme the main header names, and read an
  old-style set written to disk as `partN` as volume 1 alone (a short member, caught by
  the size check). Stream volumes are now written under the set's own scheme.
- **Encrypted RAR 2.x-4.x data** (`encryption__rar4.rar`, the RAR4 header-encrypted
  fixtures): `unar -p <right password>` writes nothing and exits 0. Refused. Encrypted
  RAR5 data, header-encrypted RAR5 included, decodes correctly with `-p`. A wrong
  password also gives exit 0 and no output, so an empty pipe for a non-empty encrypted
  member is reported as `EncryptionError`; no password at all gives exit 2.
- **A non-ASCII password** does not decrypt RAR5 data (measured with the `rar` writer
  and a password with `é`). Refused. A password starting with `-`, or holding quotes or
  backslashes, works because it is its own argv item.
- **A multi-volume RAR5 set with encrypted headers** (`tinyvol_hp.part1.rar`): Debian's
  1.10.1 decodes it with the right password, but the Homebrew bottle (XADMaster 1.10.8)
  writes nothing and exits 0 (CI's macOS leg, 2026-09-26). Refused under every
  version, since the finder does not tell 1.10.1 and 1.10.8 apart for this.

Also different from `unrar p`, and handled in the pipe layout rather than refused: an
all-entries run always includes file-version history rows, and a RAR3/4 symlink emits its
stored target as data.

Still to do: file the XADMaster bug upstream.

## stdlib `tarfile` treats a corrupt non-first header as clean end-of-archive

`tarfile.TarFile.next()` re-raises `InvalidHeaderError` only when it occurs at offset 0;
a corrupt member header anywhere later is swallowed and iteration simply ends — so
mid-archive corruption produces a **silently shortened listing**, never a
`CorruptionError`. Confirmed against a corrupted-checksum fixture and a corrupted
`.tar.gz` whose garbage decode parses as an invalid header (deep review W1).

Archivey's backstop is the end-of-archive check in `TarReader._verify_tar_eof`
(`decide-strict-archive-eof-default`, Option F). When the stopped scan lands on a
**rejected (non-null) header block**, archivey raises `CorruptionError` **by default**;
a tar that merely ended on a member boundary without the two-block null trailer is warned
via `ARCHIVE_EOF_MARKER_MISSING` (WARNING by default; a `RAISE` disposition makes it
`DiagnosticRaisedError`). In random-access mode the rejected-header detection uses
`_EofProbeStream`: after the header scan it inspects the block tarfile's final header
attempt returned (always one more `next()` before stop), so it catches the case **even
when the bad header is the archive's final block** — including after a GNU sparse member —
without seeking back (no re-decompression on a compressed source).

**Streaming limitation (open).** In forward-only streaming (`streaming=True`), tarfile's
`_Stream` hides its header reads, so `_EofProbeStream` is unavailable and detection falls
back to the trailing-block check. A rejected **final** header (a corrupt header as the
archive's last block, nothing after) is therefore misclassified as `observed_kind="absent"`
and surfaces as a missing-trailer warning, not `CorruptionError`. Random access catches
this case. A native TAR header walker (the 7z/RAR strategy applied to TAR, open-issues P3)
would validate each header at its offset and close the streaming gap. Documented for users
in `docs/formats.md` and `docs/gotchas.md`.
Handbook: [`formats/tar.md`](formats/tar.md) §2.2, §7.

## TAR sparse members are extracted dense, and their holes count against `max_ratio` (by design)

A sparse member (old GNU `S` typeflag, or the PAX 0.0 / 0.1 / 1.0 encodings) is written
through the ordinary file path, so every hole becomes zero bytes on disk and in the
extraction ratio count. Measured: GNU `tar --sparse` of a 10 MiB file holding one byte of
data is a 10 240-byte archive, and `extract_all()` under the default `ExtractionLimits`
raises `ResourceLimitError` at 1024:1. With the guard relaxed the output is dense, where
`tar -x` recreates the holes. The holes count by maintainer ruling (2026-09-25): written
out, they fill the disk like any other output, so the ratio guard is right to weigh them.
A caller that expects sparse files raises `max_ratio`. Revisit if extraction ever
preserves holes, since the disk would then hold only the data. Handbook:
[`formats/tar.md`](formats/tar.md) §6.

## `max_metadata_bytes` weighs the values in `extra`, not the keys (open)

`member_metadata_bytes` sums the string values of `extra`, one level of nested dicts
included, and never counts a key. TAR keeps every PAX record as
`extra["tar.pax_headers"]`, and a PAX keyword is a string of any length, so its bytes are
retained unweighed. Measured: one member whose PAX record has a 100 000-byte keyword and a
one-byte value weighs 4 bytes. 3 000 such members gzip to about 514 KiB and list under a
1 MiB cap with about 300 MB of keywords held; only `max_members` ends that walk. Counting
keys is a change to shared listing accounting, which moves the effective cap for every
format that puts strings in `extra`.

## Pre-1970 Unix timestamps list as invalid on Windows only (open)

Unix-seconds fields are converted with `datetime.fromtimestamp(ts, tz=timezone.utc)` in
the TAR, ZIP (UT extra field), RAR and gzip paths. On Windows that goes through
`gmtime()`, which rejects negative values, so a member dated 1969 lists with
`modified=None` plus `MEMBER_TIMESTAMP_INVALID` there and with the right date on Linux
and macOS. The fix is one helper, `epoch + timedelta(seconds=ts)`, used at every site.
Not reproduced on Windows here; the behaviour is the one the ZIP reader's UT-field
comment already records.

## WinRAR 3.x SHA-1 KDF mutates its input buffer (emulated)

**Status: emulated, not an archivey bug.** WinRAR's RAR3 string-to-key runs SHA-1's
message schedule in place on the 64-byte block buffer and writes the expanded words
back little-endian. `hashlib.sha1` computes the digest of *this* update correctly
and does not touch the caller's buffer; archivey then applies the same in-place
corruption to a reused `bytearray` seed so later rounds match WinRAR. Short
passwords (UTF-16LE password + 8-byte salt ≤ 64 bytes) never hit the path.
Ported from `rarfile` 4.3 `Rar3Sha1`. Evidence: `tests/test_rar_parser.py` (digest
of the original bytes, seed mutated afterwards, long-password s2k vs `rarfile`).
The committed `-hp` fixtures use `header_password` (UTF-16LE + salt is 38 bytes),
so they never hit the mutation; that path is pinned by the unit tests, not by
listing those archives. Handbook: [`formats/rar.md`](formats/rar.md) §3.

## Importing the ISO backend patches pycdlib process-globally (by design)

`import archivey` eagerly imports the ISO backend to register it, and that import installs a
directory-cycle guard **into pycdlib's own namespace**: `iso_reader._install_pycdlib_directory_cycle_guard()`
replaces `pycdlib.pycdlib.collections` with a proxy whose `deque` subclass drops a directory
record whose extent it has already scheduled. Without it, a corrupt/crafted ISO whose directory
records close a cycle (a child extent pointing back at an ancestor) loops forever in pycdlib's
plain-`deque` tree walk — the mutation harness found a Joliet case (see
`test_pycdlib_directory_cycle_does_not_hang`).

The guard is confined to pycdlib (not a global `collections.deque` swap), installed once and
permanently, and is transparent on well-formed images (valid trees never revisit an extent). The
one thing to be aware of: a program that **also uses pycdlib directly** in the same process will
see archivey's guarded `deque` in pycdlib's namespace too. That is a deliberate trade — hang-safety
on hostile input over leaving another library's pycdlib untouched — and the guard is a strict
superset of pycdlib's own behaviour on valid trees, so it does not change correct results.

## ISO counted a member's typing-time diagnostics once per listing pass (resolved)

**Status:** resolved by the `one-member-listing-per-reader` change. Reported as K28 on
PR #386, where the same defect was first patched for ZIP.

`extract_all` used to list an indexed archive twice — once for the progress totals and
the selector, once to drive the extraction — and a backend that rebuilt its
`ArchiveMember` objects on each pass ran its typing-time diagnostics twice for one
member. ZIP was patched with a per-position report ledger in the base reader; ISO still
emitted directly, so a Rock Ridge name that normalized would have been counted twice.

The base reader now owns one member list filled by one backend walk, so every backend,
ISO included, builds each member once and emits its typing-time diagnostics once. The
ledger and its re-attach step are gone. Each typing-time diagnostic carries the walk
position as its context `member_id` on ZIP, 7z, RAR, ISO and TAR.

## Random-access accelerators on macOS (resolved)

**Status:** resolved. archivey uses a single accelerator library — `rapidgzip` — for both gzip
and bzip2, and closes every accelerator object via a `weakref.finalize` guard. With those two
measures the accelerators run cleanly on Linux, Windows, and macOS, so `AUTO` enables them on
every platform. This note records the two distinct bugs behind the long investigation.

### Symptom

With the `[seekable]` accelerators installed, a process that used them could abort with
**SIGABRT (exit code 134)** at interpreter shutdown — *after* all work had completed — with
either of:

```
Detected Python finalization from running rapidgzip thread.
terminate called without an active exception
```
```
malloc: *** error for object 0x...: pointer being freed was not allocated
```

### Bug 1 — an accelerator object must be *closed*, not just joined

`rapidgzip` / `indexed_bzip2` spawn **C++ worker threads** (`std::thread`s, invisible to Python's
`threading` module). Each installs a guard that calls `std::terminate()` if a worker thread is
still running when the interpreter is finalizing. The decisive detail: **`join_threads()` does
not stop the worker thread — only `close()` does** (the library's own message says to "close all
… objects"). So a stream finalized **without being closed** aborts, on every platform. Measured
by `tests/test_accelerator_shutdown.py` (rapidgzip, both codecs × intact/corrupt/truncated ×
cleanup, each in its own subprocess); the input variant is irrelevant — only finalization
matters:

| Cleanup strategy | Result |
|---|---|
| **closed** — `read()`, then `join_threads()` + `close()` during the run | clean |
| **raw cycle_gc** — raw object reclaimed by the cyclic GC mid-run, never closed | **abort** |
| **raw unclosed** — raw object finalized at interpreter shutdown, never closed | **abort** |
| **guarded cycle_gc / unclosed** — same two paths, but a `weakref.finalize` guard **closes** the object on finalization | clean |

**Fix:** `_AcceleratorStream` (in `archivey.internal.streams.codecs`) wraps every accelerator
object and installs a `weakref.finalize` guard that **closes** the raw object exactly once — when
the wrapper is collected (cyclically or not) or at interpreter exit — holding a strong reference
so the close always runs before the object is freed. `close()` on the wrapper triggers the same
guard early. (An earlier version of the guard called `join_threads()` only, which is insufficient
— that was the first half of the macOS abort.)

### Bug 2 — rapidgzip and indexed_bzip2 cannot coexist in one process

After Bug 1 was fixed, macOS *still* aborted — but as a `malloc … pointer being freed was not
allocated` heap corruption, and only when **both** `rapidgzip` and `indexed_bzip2` were
importable. `scripts/dual_accelerator_repro.py` isolates it (no archivey, no pytest): decompressing
through **both** libraries in one process crashes ~100% of the time on macOS, while using either
one alone — even with both imported — never crashes. The two libraries are by the same author and
statically bundle a large overlapping C++ core; on macOS, dyld coalesces their duplicate weak C++
symbols across the two dynamic libraries, so one module's allocator can free the other's objects.

**Fix:** use only `rapidgzip`. Its Python package bundles the specialized bzip2 decoder as
`rapidgzip.IndexedBzip2File`, so archivey routes **both** gzip and bzip2 through rapidgzip and
never imports the standalone `indexed_bzip2` package. The `[seekable]` extra depends on
`rapidgzip` alone. With a single accelerator library in the process, the collision cannot happen.
`tests/test_accelerator_shutdown.py::test_archivey_uses_single_accelerator_library` guards against
regressing this (it decompresses both codecs through archivey in a subprocess and asserts
`indexed_bzip2` is never imported).

This matches the library author's own guidance. From
[mxmlnkn/librapidarchive](https://github.com/mxmlnkn/librapidarchive):

> I am not sure how well the rapidgzip and indexed_bzip2 Python modules work when loaded at the
> same time. There may be name collisions resulting in problems. … Currently, I am sidestepping
> this issue in ratarmount by including indexed_bzip2 in the rapidgzip Python package because it
> is trivial and low-overhead to do so. **So, if you need to use both, depend on rapidgzip for
> now.**

Note there are two ways rapidgzip can decode bzip2: `rapidgzip.IndexedBzip2File` (the
**specialized** indexed_bzip2 code bundled into the rapidgzip Python package — full feature and
performance parity) and `rapidgzip.RapidgzipFile` opening a `.bz2` directly (a **generic**
algorithm that, per the author, "has more memory overhead and might be slightly slower"). archivey
uses `IndexedBzip2File` for parity with the standalone package.

### Bug 3 — rapidgzip terminates the process when its Python source raises (open)

**Status: open upstream defect** (present in rapidgzip 0.16.0, the current and floor version).
When a rapidgzip object decodes from a **Python file object** and any callback into that object
raises — e.g. the stream was closed underneath it — the C++ layer throws
`std::invalid_argument` ("Cannot convert nullptr Python object to the requested result type")
through a `terminate()` boundary and **aborts the process** (SIGABRT). This fires on `read()`,
on `close()`, and on the GC-time finalize guard alike, so no Python-level `try/except` — not
even the Bug 1 guard's — can contain it, and archivey's reader-boundary error translation never
gets a chance to run.

**Mitigation in archivey:** never kill the source underneath a live accelerator stream. The
single-file reader's `_close_archive` deliberately does **not** close the (non-owning)
`SharedSource` behind stream-source member streams, so `reader.close()` with a member stream
still open cannot trigger the abort (and member streams stay readable after reader close, as
with every other backend). The remaining trigger — the **caller**'s own stream fails or is
closed while an accelerator-backed stream still reads it — is contained in Python rather than
fixed: every rapidgzip decoder (gzip / zlib / deflate, and bzip2 since the 2026-09-25 catch-all
review; before that the bzip2 path aborted) reads a caller-owned stream through
`_TrappingSource` in `codecs.py`, which parks the callback's exception and returns an
EOF-shaped value, and `_AcceleratorStream` re-raises it as an ordinary Python exception after
the call. See `dev-docs/topics/exception-handlers.md` §C-boundary trap. Only an upstream fix
removes the need for the shim. Path sources are unaffected (rapidgzip owns an independent
handle) for the *Python-source-raises* trigger. Separately, some **path**-source truncations /
CRC mismatches can still `std::terminate` during worker finalization after a Python exception —
see `dev-docs/investigations/rapidgzip-upstream-report.md` §2. The stdlib codec fallbacks raise
a normal `ValueError`, which the reader boundary translates to `UnsupportedOperationError`.

### Soft EOF on truncated gzip (by design — not a bug)

**Status: upstream design / Archivey mitigated on any seekable source** (rapidgzip 0.16.0). The
parallel reader often returns empty or a short/full prefix **without raising** on truncated
input (`block_offsets_complete=True` is not trustworthy). Archivey backstops gzip on **any
seekable source** (path or caller-owned `BinaryIO`) with empty→stdlib fallback plus a
single-member ISIZE compare; multi-member ISIZE sum is deferred. Full write-up:
[`rapidgzip-upstream-report.md`](investigations/rapidgzip-upstream-report.md). End-user note:
[Gotchas](../docs/gotchas.md#format-limitations). **Not filed upstream** (would be a feature request
for an incompleteness flag, not a bug report).

### rapidgzip's index does not expose gzip member boundaries (0.16.0)

**Status: confirmed limitation — no action.** The multi-member disambiguation the ISIZE backstop
uses (a byte scan for a further `1f 8b 08` header) cannot be replaced by rapidgzip's random-access
index: `block_offsets()` / `available_block_offsets()` expose **seek points chosen for chunked
random access**, not gzip *member* (stream) starts. Empirically (2- and 3-member files with
distinct member sizes, read to EOF), member boundaries never appear in the offsets at any
`parallelization` — serial (`parallelization=0`, archivey's mode) records only `{start, EOS}`,
and parallel adds mid-member chunk points unrelated to member starts. There is no member/stream
**count** accessor (`add_deflate_stream_crc32` / `set_deflate_stream_crc32s` are index-import
inputs, not a decode-time enumeration). So the byte scan stays, and the deferred **per-member
ISIZE sum** — which would need the same member data — cannot use the index either. (Closed the
`gzip-multimember-detect-via-index` change proposal on this finding.)

### The canary

`tests/test_accelerator_shutdown.py` asserts the contract for Bug 1: the **closed** case and the
two **guarded** finalization paths exit cleanly on every platform (if they ever abort, archivey's
own cleanup is broken), while the **raw** `cycle_gc` / `unclosed` paths abort. If a future
`rapidgzip` release stops aborting on a raw, never-closed object (e.g. it closes/joins in its
destructor), the raw-case assertions **fail** — the signal that the close-on-finalize guard is no
longer load-bearing and the wrapper could be simplified.

### Debugging tools

- `scripts/dual_accelerator_repro.py` — confirms the two-library coexistence crash (Bug 2) and
  that routing both codecs through rapidgzip alone is safe.
- `scripts/accel_leak_trace.py` — runs the test suite with the accelerators force-enabled,
  records each accelerator stream's creation stack, and reports any left un-closed at shutdown.
  Per-test process / owning-stream leaks are a different gate: `tests/leak_oracle.py`.
- `scripts/macos_accelerator_debug.py` — characterises the finalization behaviour (Bug 1) across
  raw vs. guarded × cleanup strategies, each in its own subprocess.

## Intermittent `pyppmd` native aborts on valid PPMd streams (mitigated + stress CI)

**Status: root cause pinpointed to the pyppmd 1.3.0 `ThreadDecoder.c` rewrite (upstream
PR miurahr/pyppmd#126); mitigated in archivey by bounding every decode; not yet fixed
upstream (no issue filed there as of 2026-07-16 — ready-to-file draft in
`dev-docs/investigations/pyppmd-upstream-report.md`). Linux and Windows both affected (different
trigger shapes).** Not adversarial input — happy-path encode/decode of valid PPMd data.

### Root cause (valgrind-confirmed; refined 2026-07-23)

pyppmd runs `Ppmd7Decoder.decode` on a native worker thread. The 1.3.0 rewrite (#126)
removed the worker loop's input-empty stop condition, and `_ppmdmodule.c` translates
`max_length=-1` into an `INT_MAX` symbol budget — so on PPMd7 (no end mark) the worker
decodes **past the true end of the stream**, and when its input runs out it is left
**blocked in the reader** rather than finishing. The **corrupting write itself is a
use-after-free of pyppmd's own output buffer**: `OutputBuffer_Finish`
(`_ppmdmodule.c:552`) frees the decode's output block while that blocked worker still
holds a raw pointer into it, and a later wake (the next call, or `Ppmd7T_Free` at
teardown) resumes the worker to free-run into the freed block at `ThreadDecoder.c:134`.
Valgrind pins that as the first error in every crash family; the desynchronized 7-Zip
model is the *source* of the garbage symbols, not the first out-of-bounds write. The
after-eof guard 1.3.0 added went to the cffi backend only; the C extension has none, so
`decode(b"\0", -1)` after eof restarts the runaway worker (a hot trigger). Sized decodes
stop exactly at the payload boundary — the worker finishes and is joined — which is why
they never crash, and why py7zr (which always passes `max_length`) never sees this.

Full source-level analysis, valgrind evidence, the 1.2.0→1.3.0 regression window, and
the corrected, ready-to-file upstream report are in
`dev-docs/investigations/ppmd-native-investigation-results.md` (§D root cause, §J report). The
older `dev-docs/investigations/pyppmd-upstream-report.md` is folded into a pointer to it (it had
attributed the corruption to the model walk; §J corrects that to the output-buffer UAF).
The deterministic valgrind gate is `scripts/ppmd_uaf_valgrind.py`.

### Random input also corrupts, sized decode or not (found 2026-09-25)

The "not adversarial input" line above describes how the defect was found, not its
reach. Feeding random bytes — which is what a wrong 7z AES key hands the PPMd coder, and
what a hostile archive can hand it directly — through archivey's own bounded `Codec.PPMD`
path (order 6, 16 MiB, `unpack_size` and `pack_size` set) makes
`Ppmd7Decoder.decode` return `NULL` without setting an exception: every decode of
`random.Random(1).randbytes(256 * 1024)` surfaces as `CorruptionError` wrapping
`SystemError: ... returned NULL without setting an exception`. That is the C extension
reporting failure with its state already inconsistent. In a run of a few hundred such
decodes in one process, after other codecs had run, the process died with SIGSEGV
inside `decode` (faulthandler: `decompress.py` `_decode` → `Ppmd7Decoder.decode`). Found
while measuring codec rejection for `bounded-password-confirmation`; that change keeps PPMd
non-rejecting and never feeds it random input in-process in tests. Password confirmation
decoding a wrong key into PPMd predates the change. Tracked internally.

### Windows: `STATUS_HEAP_CORRUPTION` on fresh PPMd children

On `windows-latest` the suite has intermittently aborted during
`tests/test_sevenzip_reader.py::test_py7zr_codec_fixtures_roundtrip` with
`Windows fatal exception: code 0xc0000374` (`STATUS_HEAP_CORRUPTION`). Re-runs of identical
commits often pass. Early reports noted Windows/py3.14; isolation later pinned the same
abort on **Windows/py3.11** as well (`pyppmd==1.3.1` win_amd64). Windows/py3.14 can still
pass on the same commit that fails py3.11.

Per-label subprocess isolation (#80) + a dedicated stress run produced:

| Field | Value |
|-------|--------|
| Label / filters | `ppmd` / `("PPMD",)` |
| Exit | `0xC0000374` (`STATUS_HEAP_CORRUPTION`) |
| Library | `pyppmd` 1.3.1 |
| First pin phase | `read_member:nested/beta.bin:start` (after `alpha.txt` OK) |
| Stress pin (50×) | **2/50** on py3.11 at `read_member:alpha.txt:start`; 0/50 on py3.14 that run |
| Stack | `_open_member` → `skip_forward` / decode → `pyppmd` |

**Fresh PPMd-only subprocesses are enough** — prior pytest cases are not required. The
fixture is a py7zr-built solid PPMd archive from plain members (`b"alpha\n" * 100`,
`bytes(range(64)) * 16`).

### Linux: SIGSEGV / `malloc(): invalid size` after other-codec warmup

Independently, stress on Linux reproduced a **highly flaky** native abort when other 7z
codecs are exercised in the same process **before** PPMd (`warmup_codecs` scenario):

| Observation | Detail |
|-------------|--------|
| Rate | ~**10/30** children (~1/3) in one local soak; also seen on a single first run |
| Signals | `SIGSEGV` (−11) and `SIGABRT` (−6) with `malloc(): invalid size (unsorted)` |
| Typical phase | PPMd read after LZMA2/Deflate/Bzip2 warmup (`read_member:…:start` or stream open) |
| `fresh_baseline` alone | 0/20 crashes in the same soak |
| Raw `pyppmd` encode/decode alone | 0/40 subprocesses |
| Raw archivey `PpmdDecompressorStream` alone | clean in short soaks |
| Warmup **without** PPMd (LZMA2/Deflate/Bzip2 only) | **0/30** crashes |
| Same warmup **then** PPMd | **10/30** crashes |

So the Linux abort **is PPMd-related** — other-codec warmup alone does not fire it; PPMd
after that warmup does. It still looks like process-wide native state interacting with
`pyppmd` (not a pure “any native codec” crash). Windows can also fail on a minimal fresh
PPMd 7z child; stress runs often show `raw_*` clean and `warmup_codecs` hot on both OSes.
Treat per-scenario rates as the comparison table.

### Version matrix (Linux, `warmup_codecs`, unbounded `decode(..., -1)`)

To check whether the native abort is recent, the same stress was run across published
`pyppmd` versions with archivey’s PPMd adapter forced back to unbounded `max_length=-1`
(the pre-`unpack_size` behavior). 40 children each:

| pyppmd | native crashes | other failures | passes |
|--------|----------------|----------------|--------|
| 1.1.1 | **0**/40 | 27 (CRC mismatch on solid 2nd member) | 13 |
| 1.2.0 | **0**/40 | 27 (same CRC pattern) | 13 |
| 1.3.1 | **12**/40 (`SIGSEGV`/`SIGABRT`) | 0 | 28 |

`pyppmd==1.3.0` has no installable artifact on this platform (PyPI 404 / resolver miss);
1.3.1 (2025-11-27) is the first 1.3.x wheel we could run. The 1.3.0/1.3.1 git delta includes
threaded-decoder buffer/EOF changes (`ThreadDecoder.c`, #126).

**Conclusion:** the Linux native abort reproduces on **1.3.1** and not on **1.1.1/1.2.0**
under the same unbounded decode path — the regression is the 1.3.0 `ThreadDecoder.c`
rewrite (see “Root cause” above). Older versions instead return wrong bytes (CRC fail)
on solid multi-member reads rather than aborting: their worker stopped at input-empty,
which prevented the runaway but also cut symbols short at chunk boundaries.

With the current `unpack_size`/`max_length` bound (see below), the same Linux
`warmup_codecs` soak was **0/80** crashes on 1.3.1 — so bounding decode is an effective
mitigation even on the crashy wheel.

**Version floor decision:** the `[recommended]` extra now requires **`pyppmd>=1.3.1`**. Pinning
older is worse on every axis: 1.1.x/1.2.0 silently return *wrong bytes* on chunked
bounded decodes (quiet data corruption beats a crash only if you never notice it),
`py7zr` ≥1.1 hard-requires `pyppmd>=1.3.1` (dependency conflict with a would-be 7z-writing
extra and the test oracle), and 1.3.1 is the first line with CPython 3.14 wheels. With
the floor raised, the 1.1.x premature-eof recovery pumps were removed from
`PpmdDecoder.flush` — it now injects at most the one documented extra NUL and reports
anything still missing as truncation.

Repro (non-blocking stress entry points):

```bash
uv run --no-sync python scripts/ppmd_native_stress.py 30 --scenarios warmup_codecs
uv run --no-sync pytest -m ppmd_native_stress -k warmup --timeout=600 -o addopts=
```

**Minimal upstream-facing repro (no archivey):** `scripts/pyppmd_crash_repro.py`
depends only on ``pyppmd`` (+ stdlib). Two crash families on 1.3.1:

| mode | what | ~crash rate (5 cycles/child) |
|------|------|------------------------------|
| `extra-null` | sized to eof, then `decode(b"\\0", -1)` | ~40% (up to 30/30 seen) |
| `overshoot` | `decode(packed, -1)` only | ~15–25% (19/30 seen) |
| `sized-safe` / `pre-eof-null` / `skip-after-eof` | controls | 0% |
| `underfed-sized` / `hostile-tail` | adversarial-shape controls (truncation, garbage tail) | 0% |

```bash
pip install 'pyppmd==1.3.1'
python scripts/pyppmd_crash_repro.py 30 --mode extra-null
python scripts/pyppmd_crash_repro.py 30 --mode overshoot
python scripts/pyppmd_crash_repro.py 30 --mode sized-safe
```

**Archivey mitigation:** on the 7z/ZIP paths we avoid both crash families by being
careful — (1) always pass folder/member ``unpack_size`` as ``max_length`` (no PPMd7
``-1`` overshoot); (2) never call native ``decode`` with ``max_length=-1`` after eof;
(3) at compressed EOF, ``flush`` injects at most **one** documented extra NUL (bounded
by remaining size) and reports anything still missing as ``TruncatedError`` — fabricated
input is never pumped in a loop, so truncated/hostile data cannot be silently completed
with garbage.

**The exact bound is what matters — “bounded” is not enough.** A/B soaks showed sized
requests that exceed the stream's true remaining output by ≳64 KiB crash 1.3.1 **without
any ``-1``** (`oversized` mode: +65536 over → 13/20 and 10/20 in two soaks; +64 / +4096
over → 0/20 each; large multi-chunk members with the exact bound → 0/20). Consequences:

- **Unsized PPMd7 is rejected at construction** (`ValueError`): with no end mark and no
  declared size there is no safe request size — and no correct output boundary anyway.
  The 7z header always provides the folder size, so no product path is affected.
- **Unsized PPMd8 stays supported** (end mark stops the native worker on valid data);
  it decodes via bounded 64 KiB requests in a drain loop, never ``-1``. ZIP always
  passes the member size in practice.
- **Residual hostile-input gap (upstream-only fix):** a crafted 7z/ZIP header that
  inflates ``unpack_size`` ≳64 KiB past the member's true content puts the one decode
  call into the crashy class. Archivey cannot detect the lie before decoding; this
  stays a threat-model item on par with other native-codec robustness assumptions
  until pyppmd is fixed. (Small inflations measured cold; CRC checks catch the
  garbage-output side after the fact.)
The required-suite Windows PPMd roundtrip skip was removed under this contract; the
non-blocking stress job still watches for regressions. Adversarial shapes a damaged or
hostile archive can force (truncation, early close mid-member, inflated declared size
with a garbage tail) are pinned as deterministic tests in
``tests/test_ppmd_raw_streams.py`` and as subprocess soak modes in
``scripts/pyppmd_crash_repro.py`` — all 0-crash on 1.3.1 with bounding in place.

### Mitigation in the required CI matrix

- Required-suite PPMd roundtrip runs on all platforms (including Windows); decode is
  bounded by folder unpack size.
- Other Windows codec labels keep per-label subprocess isolation.
- Default pytest excludes `-m 'not ppmd_native_stress'` so stress tests never fail the
  required suite.
- Deterministic **raw** PPMd coverage (no 7z) lives in `tests/test_ppmd_raw_streams.py`
  (always passes ``unpack_size`` for PPMd7).
- In-process PPMd7 create/destroy loops remain skipped on Windows (stress job covers that).

### Non-blocking stress check (investigation vehicle)

Workflow **PPMd native stress** (`.github/workflows/ppmd-native-stress.yml`) on every PR /
main push:

- `windows-latest` + `ubuntu-latest` × Python **3.11 and 3.14**
- `scripts/ppmd_native_stress.py` (ASCII-safe console I/O) + `pytest -m ppmd_native_stress`
- Exit non-zero when any child crashes (visibility only — **do not** make this a required
  check)

Default scenarios favour the **minimal surface**, then the original 7z baseline, then
warmup:

| Scenario | Surface | Notes |
|----------|---------|--------|
| `raw_pyppmd7` / `raw_pyppmd8` | bare `pyppmd` only | No archivey, no 7z |
| `raw_archivey_ppmd7` / `raw_archivey_ppmd8` | `PpmdDecompressorStream` / `open_codec_stream` | No 7z container |
| `fresh_baseline` | py7zr PPMd 7z + archivey read | Original CI fixture |
| `warmup_codecs` | LZMA2→Deflate→Bzip2 then PPMd | Linux ~1/3 abort repro |
| `same_process` / `fresh_varied` | optional | Reuse / payload-shape axes |

```bash
uv sync --group dev --extra all
uv run --no-sync python scripts/ppmd_native_stress.py
uv run --no-sync python scripts/ppmd_native_stress.py --scenarios raw_pyppmd7 raw_archivey_ppmd7
ARCHIVEY_PPMD_STRESS_ITERS=30 uv run --no-sync python scripts/ppmd_native_stress.py --scenarios warmup_codecs
```

### Next steps

- **File the upstream issue** — the ready-to-file draft (root cause, repro, crash-rate
  tables, suggested fixes) is `dev-docs/investigations/pyppmd-upstream-report.md`; the repro
  script is self-contained (`pyppmd` + stdlib). No matching issue existed upstream as
  of 2026-07-16.
- When a fixed pyppmd ships, run the verification checklist at the end of that report;
  the unbounded-decode guards in `PpmdDecoder` stay regardless (older wheels remain on
  PyPI, and bounding is correct behavior anyway).
- The earlier open questions (is it archivey's wrapper? the 7z path? warmup-only?) are
  resolved: it is pure `pyppmd` (crash reproduces with no archivey imports), warmup only
  shifts allocator layout, and sized decodes are structurally safe.

## `pyppmd` exit-after-green abort (`test_ppmd_raw_streams` teardown)

**Status: partially mitigated (2026-07-23).** Separate fingerprint from the
mid-decode / `warmup_codecs` / unbounded-`decode(..., -1)` bug above. The
**decode-time overshoot** (large NUL flush on truncated streams) is fixed, and a
**`quiesce-on-close`** step now drives a parked worker to `finished` before the
decoder is freed so `Ppmd7T_Free` cannot resume it into the freed output block
(the same UAF, at teardown). Both mitigations attack the same defect: never leave
a native worker blocked at dispose. Required CI **still** keeps
`--allow-exit-after-green` for this module — see the caveat below. Full lab notes:
`dev-docs/investigations/ppmd-exit-after-green-exploration.md`; corrected root cause and the
quiesce measurement: `dev-docs/investigations/ppmd-native-investigation-results.md` (§D, §I).

### Symptom (pre-mitigation)

1. Run `tests/test_ppmd_raw_streams.py` in its **own** pytest process (coverage off).
2. All tests pass; breadcrumb `sessionfinish exit=0`.
3. Child dies on interpreter teardown / pytest GC with SIGSEGV or
   `corrupted size vs. prev_size` (or, in a pyppmd-only env, often mid-suite
   during truncated-flush tests).
4. Fatal module lists often included `rapidgzip`/lz4/brotli — **red herrings**
   (import-time loads via `codecs._optional`); a pyppmd-only venv crashed *more*
   often (31/40) with only `pyppmd.c._ppmd` loaded.

### Root cause

Two stacked issues on pyppmd 1.3.x:

1. **Dangerous archivey pattern (fixed):** `PpmdDecoder.flush()` injected the
   documented extra NUL with `max_length = remaining unpack_size`. On a truncated
   mid-stream member that remaining is still large; `decode(b"\0", large)` is the
   same overshoot class as unbounded `decode(..., -1)` and corrupts the heap.
   Bare-`pyppmd` repro of that shape: **85/100** children SIGSEGV; with
   `max_length` capped to 64: **0/100**. Happy-path tests alone: **0/40**.
2. **Upstream `Ppmd7T_Free` race (now mitigated in-process; still upstream):**
   tearing down a decoder whose worker is still blocked on input lets
   `Ppmd7T_Free` resume that worker into the output block already freed by the
   previous `decode` — the same output-buffer UAF as the overshoot bug, just
   reached at teardown (valgrind: `ThreadDecoder.c:134`; see the results doc §D).
   `PpmdDecoder` now **quiesces** a parked worker before dispose (bounded
   `decode(b"\0", 1)` until it exits on budget), wired through `Decoder.close()`
   (called from `DecompressorStream.close()` and on seek/recreate) plus a `__del__`
   safety net. Unfinished-decoder adversarial tests still run in **subprocess
   children**; `_run_ppmd_child` still accepts teardown signal death after a green
   `ok` body. See the caveat under *Residual*.

### Mitigation in archivey

- Cap extra-NUL recovery output to `_PPMD_EXTRA_NUL_MAX_OUTPUT` (64) in
  `PpmdDecoder.flush` / empty-`feed` NUL injection; at most one synthetic NUL;
  chunked empty drains when finishing a complete pack
  (`src/archivey/internal/streams/decompress.py`).
- Gate post-eof empty drains on ``pack_size``: drains run only when
  ``fed_compressed >= pack_size`` (**known-complete**) **and** a container
  ``unpack_size`` bounds them. Unknown/short ``pack_size`` and unsized decodes get a
  single capped NUL only — no chunked empty drains.
- **Require ``pack_size`` for PPMd7.** Without it a premature native ``eof`` (pyppmd
  flips it early on a small ``max_length`` over compressible data) is
  indistinguishable from truncation: draining to finish the tail can ``MemoryError``
  on 1.3.x (measured **36/36** on ~50–99 % pack cuts), so the decoder must refuse the
  drain — which silently truncates a *valid* member on chunked reads. 7z always knows
  the pack length, so PPMd7 requires it (`PpmdDecoder.__init__`) rather than choosing
  between truncation and a crash.
- **Plumb ``pack_size`` through the 7z pipeline.** A standalone PPMd folder reads it
  from the sized pack slice (`compressed_input_size`); an **encrypted** PPMd folder
  feeds PPMd from an AES decrypt stream with *no* knowable length, so the pipeline sets
  `_CodecStage.pack_size` from the preceding coder's output size
  (`sevenzip_pipeline.plan_folder`). Without this, encrypted-PPMd chunked/streamed
  reads truncated (now covered by `test_encrypted_ppmd_chunked_reads_roundtrip`).
- **Unsized PPMd8 gets no post-eof drain.** Its end mark terminates valid decodes; a
  drain without an ``unpack_size`` clamp only fabricates trailing bytes (measured: +3
  on an all-zero payload). Sized PPMd8 keeps the drain.
- **Quiesce a parked worker before dispose** (`PpmdDecoder._quiesce_worker`, via
  `close()` / `__del__`): drive it to `finished` with bounded `decode(b"\0", 1)` so
  `Ppmd7T_Free` becomes a no-op. Deterministic evidence:
  `scripts/ppmd_uaf_valgrind.py` — the archivey scenarios report **0** memcheck
  errors, the bare-pyppmd overshoot reproducer reports the UAF.
- **Stop at a spent payload.** A sized `decode` that returns short of its request, at
  native `eof`, with every compressed byte fed, means the payload has nothing left
  (`PpmdDecoder._note_decoded`). The decode path makes no further `decode` after
  that — the next one would resume a worker parked on empty input, which reads past
  the input buffer and surfaces as a bare `MemoryError` — and `flush` reports
  `TruncatedError`.
  Reached from a 7z folder that overstates `unpack_size` (#315 thread K6) and from a
  wrong AES key (section below). `_quiesce_worker` sends its NUL in that state even at
  `eof`: without it valgrind shows the Free-time invalid write, and a later decoder in
  the same process started from corrupted state.
- **Cap each request at a C `int`** (`_PPMD_MAX_REQUEST`): pyppmd parses `length` as
  one, and a larger value raised `OverflowError` reading a PPMd member over 2 GiB
  (`readall()` asks for the whole remaining member in one request).
- Keep unfinished-decoder adversarial coverage in fresh subprocesses; tolerate
  child teardown abort after a successful body (`tests/test_ppmd_raw_streams.py`).

**Residual:** (1) `Ppmd7T_Free` teardown race — quiesce-on-close removes the UAF on
the shapes that reproduce it under valgrind, but it is **defense-in-depth**, not a
proven elimination: the observable abort rate on archivey's own shapes was already
~0 on Linux/CPython 3.11 here (child soak 400/400 clean at baseline), and the race
is timing/platform-dependent (hotter on 3.12+/free-threaded/Windows). Required CI
therefore **still** keeps `--allow-exit-after-green`; drop it only once the
deterministic `ppmd_uaf_valgrind.py` gate runs green on the hot-race platforms, not
on this host's soak alone. (2) Declared-complete but internally corrupt sized packs
can still fill toward `unpack_size` via empty drains (container CRC is the backstop).
(3) `pack_size` must measure the same bytes `feed()` counts — see `PpmdDecoder`
docstring invariant; the 7z plumbing satisfies it by construction (pack slice length
/ preceding coder output). (4) Wrong-key AES garbage into a *complete* PPMd pack
used to raise a different `MemoryError`, fixed by the spent-payload stop above;
section below.

### Verification (this investigation)

| Soak | Overshoot (large NUL) | Free-race residual |
|------|----------------------|--------------------|
| Bare half-pack + NUL(rem) | ~85/100 → **0/100** with cap 64 | n/a |
| Adversarial tests in subprocess | Contained | Child may still SIGSEGV after `ok` |
| Parent `test_ppmd_raw_streams` session | Soft-pass via `--allow-exit-after-green` | Intermittent exit-after-green possible |
| `ppmd_uaf_valgrind.py` (deterministic) | archivey scenarios **0 errors** | quiesce-on-close: **0 errors** with fix; bare overshoot still reproduces |

Do **not** claim the Free race is gone until the deterministic
`scripts/ppmd_uaf_valgrind.py` gate is green on the hot-race platforms
(3.12+/free-threaded/Windows) or teardown is process-isolated by default;
quiesce-on-close is defense-in-depth (see *Residual*). See also: exploration doc,
`dev-docs/investigations/ppmd-native-investigation-results.md` (§D/§I/§J),
`scripts/ci_run_native_modules.py`, `.github/workflows/ppmd-native-stress.yml`.

## AES+PPMd wrong-key `MemoryError` aborted password iteration (fixed)

**Status: fixed.** Recorded from [#344](https://github.com/davitf/archivey/pull/344)
D4; fixed by the spent-payload stop in the pyppmd section above (the same change as
#315 thread K6).

7z AES has no password check value, so confirm decrypted, decoded, and CRC'd the
folder (`SevenZipReader._password_for_folder` → `_verify_decoded_folder` at the time;
now a bounded `plan_password_confirm` / `run_password_confirm_plan` probe). A wrong key
feeds PPMd garbage. On some keys that garbage stops PPMd short of the folder's
declared size at native `eof` with the whole pack fed — the same state as a header
that overstates `unpack_size` — and the next empty drain raised `MemoryError` from
`pyppmd.Ppmd7Decoder.decode`. `PasswordManager.attempt` advances only on
`EncryptionError`, and `MemoryError` is not an `ArchiveyError`, so the candidate
loop died and a later correct password was never tried.

The fix makes no call that can raise it: the decoder stops at the short return and
reports `TruncatedError`, which confirm already remaps to `EncryptionError`, so
iteration continues. No spec carve-out for `MemoryError` was needed; it still
propagates unchanged from anywhere else.

**Fixture.** A 194-byte archive (SHA-256
`35dbb0c965d7030d5d27986d165483f9db1d923f347fb23a92acdb02d80b8dbb`; PPMd + 7zAES,
headers in the clear, one member of 51200 `a` bytes, password `secret`) where
`wrong856` and `wrong2552` hit the old `MemoryError`. It is pinned in
`tests/test_sevenzip_reader.py` (`test_aes_ppmd_wrong_key_moves_on_to_the_next_password`),
because AES salt and IV are per archive: a rebuild with `7z a` collides on a different
password. On the old code a sweep of `wrong0`..`wrong2999` over it gave
`EncryptionError=2998`, `MemoryError=2`.

## Intermittent Linux full-suite heap corruption (`[all]` / Hypothesis late crash)

**Status: open / intermittent.** Observed on GitHub Actions `ubuntu-latest` required CI
legs with `--extra all` (and `all-lowest`). **Not** the same bug as the pyppmd section
above: a green **PPMd native stress** workflow does not clear this, and the fatal stacks
are not inside a PPMd decode.

### Symptom

The required suite process dies with `Fatal Python error: Segmentation fault` (exit
139) or `Fatal Python error: Aborted` (exit 134). The Python stack at death is usually a
**late symptom** (heap already corrupt), not the corrupting call:

| Crash site (examples) | What it means |
|-----------------------|---------------|
| `Garbage-collecting` → `hypothesis/internal/charmap.py` during `test_property_safety.py::test_normalize_total_and_idempotent` (strategy validate / `characters`) | Hypothesis touches the allocator; prior native work already poisoned the heap |
| `Garbage-collecting` → `subprocess._close_pipe_fds` / `Popen` in `rar_unrar.open_unrar_p` (e.g. during `test_multi_volume_stream_materialization`) | Same: GC + new subprocess while the heap is bad |

Fatal logs list extension modules along the lines of:

`backports.zstd`, `lz4`, `_brotli`, `pyppmd.c._ppmd`, **`rapidgzip`**, `bcj._bcj`, `_cffi_backend`

### Where it does / does not show up

| Environment | Observation |
|-------------|-------------|
| `ubuntu-latest` × `[all]` / `[all-lowest]` × py3.11–3.13 (sometimes 3.14) | Intermittent process death mid-suite |
| Same matrix `[core-only]` | Clean (no `rapidgzip` / optional natives from `[all]`) |
| `macos-latest` / `windows-latest` `[all]` | Typically clean on the same commits (Windows also skips `unrar` data tests) |
| Local `uv run --no-sync pytest tests/ -q` after `uv sync --group dev --extra all` | Often green; **not a reliable one-shot repro** |
| PPMd stress job (isolated children; see section above) | Consistently green on recent PRs — different surface |

CI runners use **uv’s standalone CPython** (Linux builds report Clang in `sys.version`),
with pytest-cov enabled via `addopts` (`--cov=archivey …`).

### Why mid-decode PPMd stress does not clear the full-suite flake

`.github/workflows/ppmd-native-stress.yml` / `scripts/ppmd_native_stress.py`
default scenarios run short children aimed at mid-decode / `warmup_codecs` aborts
(no long pytest session, often no `rapidgzip`). A green result there means that
probe is quiet — not that a long `[all]` process is free of native heap damage.

The separate **exit-after-green** abort of `tests/test_ppmd_raw_streams.py` (green
session, then teardown SIGSEGV/SIGABRT) is documented above as **mitigated**; do
not conflate residual full-suite Hypothesis/RAR late crashes with that fingerprint.

### Leading suspects (unconfirmed)

1. **`rapidgzip`** in a long-lived pytest process (AUTO on under `[seekable]` / `[all]`),
   possibly after truncated/corrupt gzip/bzip2 paths exercised elsewhere in the suite
   (`tests/test_accelerator_shutdown.py` already documents raw rapidgzip abort-on-
   finalize in **subprocesses**; in-process corruption is a separate question).
2. **Interaction / allocator layout**: many natives loaded together + coverage + GC,
   with Hypothesis or `subprocess` merely the tripwire.
3. **Not** the gzip/zlib truncation-recovery *logic* itself — crashes predate a stable
   local repro of that change and do not stack in `DecompressorStream` / `verify.py`.

### CI bandage (not a root-cause fix)

Required `[all]` / `[all-lowest]` jobs split the suite (`.github/workflows/ci.yml`):

```text
# 1) Main suite — skip Hypothesis + dedicated accelerator/PPMd stream modules
pytest tests/ \
  --ignore=tests/test_property_safety.py \
  --ignore=tests/test_rapidgzip_deflate_zlib.py \
  --ignore=tests/test_accelerator_shutdown.py \
  --ignore=tests/test_accelerator_corruption.py \
  --ignore=tests/test_ppmd_raw_streams.py -q

# 2) Accelerator stream tests — one subprocess each (coverage off; breadcrumbs)
python scripts/ci_run_native_modules.py

# 3) PPMd raw streams — own subprocess (coverage off). Formerly soft-passed
#    exit-after-green; that abort is mitigated (capped NUL flush + subprocess
#    unfinished-decoder tests). Hard-fail like other native modules.
python scripts/ci_run_native_modules.py \
  --modules tests/test_ppmd_raw_streams.py

# 4) Hypothesis property-safety
pytest tests/test_property_safety.py -q
```

(1)↔(4) stops a corrupted main-suite heap from taking down Hypothesis in-process
(and vice versa). (2) keeps the heaviest in-process rapidgzip ON / truncated-corrupt
accelerator paths out of the long suite. (3) is PPMd raw streams in isolation —
see **`pyppmd` exit-after-green abort** above (mitigated).

**Why accelerator modules are not one combined pytest:** a single multi-module
process aborted on Ubuntu with every test green during `coverage.collector.flush_data`
/ GC (`corrupted size vs. prev_size`, exit 134). Per-module children + disabling
cov on that leg both harden the job and name the offender.

`PYTHONFAULTHANDLER=1` is set on these steps so fatal traces always dump.

This still does **not** claim the main suite is free of every native (AUTO/SEEKABLE
paths and py7zr/PPMd corpus remain). It is CI hygiene, not a product fix.

### How to reproduce / bisect (investigation recipe)

There is **no single-command reliable repro** yet. Use rate + A/B:

```bash
# Match CI-ish env (Linux preferred; uv CPython)
uv python install 3.11
uv sync --group dev --extra all
uv run --python 3.11 --no-sync python -c "import sys; print(sys.version)"

# 1) Baseline soak — expect rare exit 139/134, not every run
for i in $(seq 1 20); do
  echo "=== pass $i ==="
  uv run --python 3.11 --no-sync pytest tests/ -q \
    || { echo "FAILED pass $i rc=$?"; break; }
done

# 2) Same soak but keep Hypothesis out of the long process (CI bandage shape)
for i in $(seq 1 20); do
  uv run --python 3.11 --no-sync pytest tests/ \
    --ignore=tests/test_property_safety.py \
    --ignore=tests/test_rapidgzip_deflate_zlib.py \
    --ignore=tests/test_accelerator_shutdown.py \
    --ignore=tests/test_accelerator_corruption.py \
    --ignore=tests/test_ppmd_raw_streams.py -q \
    || { echo "main FAILED pass $i rc=$?"; break; }
  uv run --python 3.11 --no-sync python scripts/ci_run_native_modules.py \
    || { echo "accelerators FAILED pass $i rc=$?"; break; }
  uv run --python 3.11 --no-sync python scripts/ci_run_native_modules.py \
    --modules tests/test_ppmd_raw_streams.py \
    || { echo "ppmd-raw FAILED pass $i rc=$?"; break; }
  uv run --python 3.11 --no-sync pytest tests/test_property_safety.py -q \
    || { echo "property FAILED pass $i rc=$?"; break; }
done

# 2b) Hard soak of the PPMd raw-streams exit abort (matches non-required stress step)
uv run --python 3.11 --no-sync python scripts/ci_run_native_modules.py \
  --modules tests/test_ppmd_raw_streams.py --repeat 20

# 3) A/B: no rapidgzip in the environment (uninstall after sync)
uv run --python 3.11 --no-sync pip uninstall -y rapidgzip
# re-run soak (1); if crashes vanish, rapidgzip (or its use under AUTO) is implicated
# restore with: uv sync --group dev --extra all

# 4) A/B: no pyppmd (controls the other known native)
uv run --python 3.11 --no-sync pip uninstall -y pyppmd
# re-run soak (1); green PPMd stress already suggests this alone is insufficient
```

Useful while hunting:

- `PYTHONFAULTHANDLER=1` so fatal traces always dump.
- A pytest plugin or wrapper that logs the **last N nodeids** before death (crash stacks
  are late).
- Compare against CI artifacts for a red job: look for `Fatal Python error` +
  `Extension modules:` and the test named in the stack (Hypothesis vs RAR vs other).

**Known red CI fingerprints** (gzip-zlib truncation-recovery work, 2026-07; illustrative,
not a pinned commit contract):

- Run `29829920415` — Ubuntu py3.11/3.12 `[all]` SIGSEGV in Hypothesis charmap GC;
  py3.13/3.14 completed far enough to fail a separate RAR4 assertion.
- Run `29836095815` — after RAR4 fix: Ubuntu py3.11 SIGSEGV / py3.13 SIGABRT still in
  Hypothesis; other legs green.
- Run `29836326565` — with property-safety split: Ubuntu py3.11 SIGSEGV during RAR
  multi-volume `open_unrar_p` GC (main suite), proving Hypothesis isolation alone is
  incomplete.
- Run `29969446114` — after review follow-ups: Ubuntu py3.11/3.14 `[all]` SIGSEGV at
  ~63% (GC during fixture setup / `test_rar_oracle` ← `rarfile`/`cryptography` import),
  immediately after `test_ppmd_raw_streams` → `test_rapidgzip_deflate_zlib` in collection
  order. Other matrix legs green. Motivated the accelerator/PPMd-stream process split.

### Next steps

- Get a soak rate (even 1/20) under recipe (1), then A/B rapidgzip off (3).
- If rapidgzip-linked: try to shrink to a subprocess loop that only imports/uses
  rapidgzip the way the suite does (path vs `BytesIO`, truncated members, close vs GC),
  reusing ideas from `scripts/dual_accelerator_repro.py` / `tests/test_accelerator_shutdown.py`.
- If only the long mixed suite flakes: treat as CI hygiene (more process isolation, or
  coverage/accelerator policy on Linux) rather than a product API defect.
- Do **not** fold this into PPMd stress without adding a rapidgzip + long-suite axis;
  the existing PPMd job would stay green while this remains open.
