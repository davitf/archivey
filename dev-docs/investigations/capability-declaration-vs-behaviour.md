# Capability declaration vs behaviour — measured

**Status:** finished evidence. Not a fix. Run 2026-09-05 on Linux (this Cloud
workspace), `origin/main` `0c0a71c`. Sweep script
`/tmp/capability-sweep/sweep.py`, raw dump `/tmp/capability-sweep/results.json`.

Triggered by `dev-docs/formats/rar.md`: `seekable_members=True` on RAR, 
`reader.member_streams` reports `SEEKABLE`, the member stream is a pipe. That is
one field. This run asks how many others lie the same way.

## Machine

| Thing | Value |
| --- | --- |
| Python | 3.11.16 |
| `rar` | `/usr/bin/rar` RAR 7.00 (trial) |
| `unrar` | `/usr/bin/unrar` UNRAR 7.00 freeware |
| `7z` | `/usr/bin/7z` 7-Zip 23.01 |
| `zip` | `/usr/bin/zip` Info-ZIP 3.0 |
| `skip_unless_runnable` | nothing skipped (40 corpus rows ran) |

Corpus via `tests.sample_archives.CORPUS` / `corpus_archive_path`. Representative
rows: `basic` (or `single-file` / `single-file-meta`), plus `encrypted` /
`encrypted-mixed` / `encrypted-header` / `encrypted-multi`, plus `large` for
zip / 7z / rar / tar.gz / tar.zst. Handbook `tplain.rar` / `tsolid.rar` were
not the sweep source.

## Predicates (falsifiable)

Written from `MemberStreams`, `CostReceipt`, `ArchiveInfo`, and
`ArchiveReader.close` docstrings, then run.

**A. `reader.member_streams`** (concrete reader; not on `ArchiveReader`)

- Opened with `seekable_members=True` → `MemberStreams.SEEKABLE` is set, **and**
  every FILE stream has `seekable() is True`, `read(); seek(0); read()` returns
  the same bytes, and `seek(n); read()` equals `content[n:]`.
- Opened with `concurrent_members=True` → two overlapping `open()` calls both
  read (two FILE members, or the same member twice if N=1).
- Default (`MemberStreams(0)`) → `seekable()` is False and `seek()` raises
  `io.UnsupportedOperation` or `ValueError`; a second live `open()` raises
  `ConcurrentAccessError`.
- Per-member `seekable()` disagreement inside one archive is a finding even if
  some members pass.

**B. `reader.cost` vs `reader.io_stats()`** (inside `enable_measurement()`)

- `listing_cost == INDEXED` → after `open` + `members()` with no payload read,
  `source_seek_count` is not a header-to-header walk proportional to member
  count. If `io_stats` is `None` or `source_seek_count` is 0 because the
  backend opened a `Path` without wrapping seeks, that is a measurement hole,
  not a pass.
- `listing_cost == REQUIRES_SCANNING` → listing does sequential header / tree
  work. Cheap on a tiny archive is not a fail; report the numbers.
- `listing_cost == REQUIRES_DECOMPRESSION` → listing makes
  `bytes_decompressed > 0` or `compressed_bytes_consumed > 0` in a way that
  shows the outer stream was decoded. Zero on both, with `consumed is None`
  because the source is a Path, is a hole or a lie — say which.
- `access_cost == DIRECT` → reading only the last FILE does not decompress
  earlier members' payloads (`delta bytes_decompressed` ≈ that member's size).
- `access_cost == SOLID` → the inverse: last-member read costs work on earlier
  ones (or the format is one compressed stream).
- `stream_capability == SEEKABLE` is the **archive source**, not member
  streams. A Path open should be `SEEKABLE`. A pipe is a separate measurement.

**C. `reader.info`**

- `member_count is int` → equals `len(list(reader.members()))`. `None` is
  allowed (tar, dir, iso: a count would require a scan).
- `is_solid` matches observed access cost / last-member decompress volume, and
  format reality (uncompressed zip/tar/iso/dir False; compressed tar True;
  7z/rar whatever the builder wrote).
- `is_encrypted` is header-level (7z, RAR5), not per-member. ZipCrypto /
  zip-aes members True on the member, False on the archive. Header-encrypted
  7z True. RAR per-member-only: measure vs that sentence.
- `is_multivolume` False on this corpus (no multi-volume row).

**D. Lifetime**

- After `reader.close()`, `read` on a stream from `open()` or
  `stream_members()` raises (`ValueError` / closed). Tested default and
  `seekable_members=True`. The ABC says member streams do not outlive the
  reader.

Verdicts: `OK` / `DECLARED-NOT-DELIVERED` / `DELIVERED-NOT-DECLARED` /
`UNTESTED` / `UNTESTED-NO-CORPUS` / `MEASUREMENT-HOLE` / `ERROR`.

## Registry formats with no corpus key

`list_supported_formats()` extras not in `FORMAT_KEYS` / `CORPUS.formats`.
All `FormatSupport.FULL` here. **UNTESTED-NO-CORPUS**, not passing.

| Format | Extension |
| --- | --- |
| `ArchiveFormat.LZMA_ALONE` | `lzma` |
| `ArchiveFormat.Z` | `Z` |
| TAR + LZMA_ALONE | `tar.lzma` |
| TAR + UNIX_COMPRESS | `tar.Z` |

No `skip_unless_runnable` misses on the 25 corpus keys.

## Matrix — member streams (primary + encrypted extras)

`DEFAULT` second-open and non-seekable held on every row that ran.
`CONCURRENT` overlapping `open()` held on every row (two members, or same
member twice for single-file). No `DELIVERED-NOT-DECLARED` on the default
contract.

`SEEKABLE` (opt-in) — primary rows:

| Format | Entry | Declared | Observed `seekable()` on FILE members | Verdict |
| --- | --- | --- | --- | --- |
| zip | basic, large | SEEKABLE | all True, seek works | OK |
| tar, tar.gz/bz2/xz/zst/lz4/lz/zz/br | basic (+ large tar.gz/zst) | SEEKABLE | all True | OK |
| dir, iso, iso-joliet | basic | SEEKABLE | all True | OK |
| 7z | basic, large | SEEKABLE | all True | OK |
| rar | basic, large | SEEKABLE | all True | OK |
| gz, gz-meta, bz2, xz, zst, lz4, lz, zz, br | single-file* | SEEKABLE | True (N=1) | OK |

`SEEKABLE` — encrypted extras (this is where it breaks):

| Format | Entry | Declared | Observed | Verdict |
| --- | --- | --- | --- | --- |
| zip (ZipCrypto) | encrypted, mixed, multi | SEEKABLE | all True | OK |
| zip-aes | encrypted | SEEKABLE | both members False | DECLARED-NOT-DELIVERED |
| zip-aes | encrypted-mixed, multi | SEEKABLE | plaintext True, AES members False | DECLARED-NOT-DELIVERED (disagreement inside the archive) |
| 7z | encrypted, mixed, header | SEEKABLE | all False | DECLARED-NOT-DELIVERED |
| rar | encrypted | SEEKABLE | both False | DECLARED-NOT-DELIVERED |
| rar | encrypted-mixed | SEEKABLE | `not_secret.txt` True, encrypted members False | DECLARED-NOT-DELIVERED (same pattern as tplain.rar) |

`reader.member_streams` still has the SEEKABLE bit in every failing row. The
flag is the request, not what the stream does. `_wrap_member_stream` sets
`seekable = declared ∧ is_seekable(inner)`.

## Matrix — cost / info / lifetime / source pipe

Listing after `open` + `members()`, no payload. `consumed` is `compressed_bytes_consumed`.

| Format | Entry | listing | access | is_solid | seeks | decomp on list | last-only Δdecomp / last size / total | is_encrypted | member_count | lifetime | source on Path | pipe `streaming=True` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| zip | basic | INDEXED | DIRECT | F | 7 | 0 | 12 / 12 / 41 | F | 6=6 | OK | SEEKABLE | refused even with streaming |
| zip | large | INDEXED | DIRECT | F | 7 | 0 | 64014 / 64014 / 192042 | F | 3=3 | OK | SEEKABLE | — |
| zip, zip-aes | encrypted* | INDEXED | DIRECT | F | 7 | 0 | ≈ last size | **F** (members True) | match | OK | SEEKABLE | — |
| 7z | basic | INDEXED | SOLID | T | 4 | 0 | **41 / 16 / 41** | F | 7=7 | OK | SEEKABLE | refused |
| 7z | large | INDEXED | SOLID | T | 4 | 0 | **192042 / 64014 / 192042** | F | 3=3 | OK | SEEKABLE | — |
| 7z | encrypted, mixed | INDEXED | SOLID | T | 4 | 0 | Δdecomp = total | **T** (folder, not header) | match | OK | SEEKABLE | — |
| 7z | encrypted-header | INDEXED | SOLID | T | 9 | 0 | Δdecomp = total | T (header) | match | OK | SEEKABLE | — |
| rar | basic | INDEXED | DIRECT | F | **0** | 0 | 12 / 12 / 41 | F | 6=6 | OK | SEEKABLE | refused |
| rar | large | INDEXED | DIRECT | F | **0** | 0 | 64014 / 64014 / 192042 | F | 3=3 | OK | SEEKABLE | — |
| rar | encrypted, mixed | INDEXED | DIRECT | F | **0** | 0 | ≈ last size | **T** (per-member only) | match | OK | SEEKABLE | — |
| tar | basic | REQUIRES_SCANNING | DIRECT | F | 3 | 0 | 12 / 12 / 41 | F | None | OK | SEEKABLE | FORWARD_ONLY, 6 members |
| tar.gz etc | basic | REQUIRES_DECOMPRESSION | SOLID | T | **0** | **0** | 12 / 12 / 41 | F | None | OK | SEEKABLE | FORWARD_ONLY |
| tar.gz | large | REQUIRES_DECOMPRESSION | SOLID | T | 0 | 0 | 64014 / 64014 / 192042 | F | None | OK | SEEKABLE | — |
| dir | basic | REQUIRES_SCANNING | DIRECT | F | **0** | 0 | 16 / 16 / 41 | F | None | OK | SEEKABLE | not piped (directory) |
| iso | basic | INDEXED | DIRECT | F | **28** | 0 | 16 / 16 / 41 | F | **None** (listed 7) | OK | SEEKABLE | refused |
| iso-joliet | basic | INDEXED | DIRECT | F | 25 | 0 | 16 / 16 / 41 | F | None | OK | SEEKABLE | refused |
| single-file* | single-file | INDEXED | DIRECT | F | 0–1 | 0 | 44 / size-or-None / — | F | 1=1 | OK | SEEKABLE | FORWARD_ONLY |

`is_multivolume` was False on every row. No multi-volume corpus entry.

Cost verdicts worth a name:

| Claim | Format / entry | Verdict |
| --- | --- | --- |
| INDEXED (seek count not ∝ N) | zip, 7z | OK (zip 7 seeks on 3 and 6 members; 7z 4 seeks on 3 and 7) |
| INDEXED | iso / iso-joliet | OK against the enum's own example ("ISO directory tree at open"), but 25–28 seeks on a 7-member image is a tree walk. `member_count` stays None because the same function comments "counting requires walking the tree". |
| INDEXED | rar | **MEASUREMENT-HOLE** (`source_seek_count` 0 on basic and large). The native parse may `read()` without `seek()`. The enum docstring already admits RAR walks every header at open and calls that INDEXED because the table is cached by `members()` time. Unfalsifiable here, not a pass. |
| REQUIRES_SCANNING | tar | OK-ish: 3 seeks, 0 decomp. Numbers only. |
| REQUIRES_SCANNING | dir | **MEASUREMENT-HOLE** (no seek wrap; `os.walk` is not a seek). |
| REQUIRES_DECOMPRESSION | compressed tar | **MEASUREMENT-HOLE**. Path goes to the codec as a path string; `consumed` is None ("static size known"); listing `bytes_decompressed` stays 0. |
| DIRECT last-member | zip, tar, iso, dir, rar (corpus is non-solid) | OK where measured (`Δdecomp ≈ last size`) |
| SOLID last-member | 7z | OK (`Δdecomp = total payload`) |
| SOLID last-member | compressed tar | **MEASUREMENT-HOLE**. `Δdecomp ≈ last size` only — the wrap counts the opened member, not skipped tar payload. The format is still one gzip stream. |
| `is_encrypted` header-only | zip / zip-aes | OK (archive False, members True) |
| `is_encrypted` header-only | 7z encrypted (no `-mhe`) | docstring vs code: True because `has_encrypted_folders` |
| `is_encrypted` header-only | rar encrypted / mixed | docstring vs code: True because `any(m.is_encrypted)` |
| `is_encrypted` header-only | 7z encrypted-header | OK (True) |
| source SEEKABLE on Path | all Path/dir rows | OK |
| source FORWARD_ONLY on pipe | tar*, single-file | OK (`streaming=True`) |
| source on pipe | zip, 7z, rar, iso | open raises `StreamNotSeekableError` even with `streaming=True`. Not a capability lie; no `CostReceipt` to read. |
| lifetime | all 40 rows, default and seekable | OK (`ValueError: I/O operation on closed file.`) |

## Violations

### 1. SEEKABLE declared, inner is a pipe — DECLARED-NOT-DELIVERED

Same lie as the handbook RAR note, on more than RAR.

Mechanism: `reader.member_streams` echoes the open flag;
`ArchiveStream.seekable()` is `declared ∧ is_seekable(inner)`. Direct/sliced
inners (stored RAR, ZipCrypto, uncompressed 7z folder decode that still wraps
a seekable buffer, tar/zip/iso/dir) pass. `unrar` stdout, 7z AES folder
decode, and WinZip AES do not.

**Corpus rar `basic` / `large` do not reproduce it.** Every FILE is
`CompressionAlgorithm.STORED`; `_can_direct_read` is True; seek works. The
committed fixtures never take the unrar data path unless the member is
encrypted (or would be compressed — the builder stored these).

**Corpus rar `encrypted-mixed` does** — within one archive:

| Member | encrypted | seekable() |
| --- | --- | --- |
| `not_secret.txt` | False | True |
| `secret.txt` | True | False |
| `also_secret.txt` | True | False |

That is the tplain.rar pattern: members disagree inside one archive. Solid is
not the distinguishing feature. Encrypted (unrar) vs stored (slice) is.

**Minimal repro** (ran here):

```bash
cd /workspace && uv run --no-sync python -c '
from pathlib import Path
import tempfile
from archivey import open_archive
from tests.sample_archives import CORPUS, corpus_archive_path
e = next(x for x in CORPUS if x.id == "encrypted-mixed")
p = corpus_archive_path(e, "rar", Path(tempfile.mkdtemp()))
with open_archive(p, password=e.passwords, seekable_members=True) as r:
    print("declared", r.member_streams)
    for m in r.members():
        if not m.is_file: continue
        f = r.open(m)
        print(m.name, "enc", m.is_encrypted, "seekable", f.seekable())
        f.close()
'
```

Output: `declared MemberStreams.SEEKABLE`; `not_secret.txt enc False seekable True`;
`secret.txt` / `also_secret.txt` `seekable False`.

Same shape for zip-aes mixed (plaintext True, AES False) and every encrypted
7z row (all False). ZipCrypto encrypted ZIP: all True — so this is not
"encryption" as a class, it is "inner handle is a pipe".

### 2. `ArchiveInfo.is_encrypted` vs its docstring — docstring/implementation conflict

Docstring: "Header-level encryption (7z, RAR5) — not per-member encryption".

| Row | Archive `is_encrypted` | What is actually encrypted |
| --- | --- | --- |
| zip / zip-aes encrypted* | False | per-member only — matches docstring |
| 7z encrypted-header | True | header (`-mhe`) — matches |
| 7z encrypted / mixed | True | folders; not header. Code: `is_header_encrypted or has_encrypted_folders` |
| rar encrypted / mixed | True | per-member passwords. Code: `has_header_encryption or any(m.is_encrypted)` |

Call this DECLARED-NOT-DELIVERED against the docstring, or a docstring bug.
The zip column did what the sentence says; RAR and 7z did not.

**Minimal repro:**

```bash
cd /workspace && uv run --no-sync python -c '
from pathlib import Path
import tempfile
from archivey import open_archive
from tests.sample_archives import CORPUS, corpus_archive_path
e = next(x for x in CORPUS if x.id == "encrypted")
for key in ("zip", "rar", "7z"):
    p = corpus_archive_path(e, key, Path(tempfile.mkdtemp())/key)
    pw = e.passwords[0] if key == "7z" else e.passwords
    with open_archive(p, password=pw) as r:
        print(key, "archive", r.info.is_encrypted,
              "members", [m.name for m in r.members() if m.is_encrypted])
'
```

Output here: `zip archive False`; `rar archive True`; `7z archive True`.

### 3. MEASUREMENT-HOLE — `io_stats` cannot see the work

`enable_measurement()` was on. `io_stats` was never `None`. The holes are
zeros that do not mean "no work":

- **RAR listing INDEXED:** `source_seek_count=0` on 6-member and 3-member
  archives. Cannot check the handbook's "41 seeks on 40 members".
- **dir REQUIRES_SCANNING:** seeks=0. Directory walk is not a seek.
- **compressed tar REQUIRES_DECOMPRESSION / SOLID:** listing decomp=0,
  `consumed=None` (Path static size). Last-member `Δdecomp` equals last size
  (member wrap), not the skipped prefix. 7z SOLID *is* visible (`Δdecomp =
  total`) because folder decode is counted.
- **`compressed_bytes_consumed`:** `None` on every Path open. Documented
  ("None when the source size is statically known"). Useless for these
  predicates on the normal `open_archive(path)` path.

### 4. UNTYPED / UNDECLARED

- **`member_streams` is not on `ArchiveReader`.** `dir(ArchiveReader)` has
  `cost`, `info`, `stream_members`, not `member_streams`. The type's own
  docstring says it is "reachable at runtime but not part of the typed public
  contract". Callers who branch on it (the handbook already does) are using
  an untyped field, and that field is the request mask, not delivery.
- **No per-member stream capability.** The archive-level flag cannot be true
  when stored and piped members share one reader. That is why tplain.rar /
  encrypted-mixed rar / zip-aes mixed disagree member-by-member with no
  diagnostic.
- **`test_member_stream_contract.py` parametrize omits RAR** (and zip-aes,
  encrypted 7z). The suite already asserts `f.seekable() is True` at line
  186. It missed this because the builder list at 107–114 is hand-written.
  Registry-vs-corpus is not the enrolment list that suite uses.

No `DELIVERED-NOT-DECLARED` hits on the stream flags: default streams did not
seek; overlapping open without `CONCURRENT` raised `ConcurrentAccessError`.

No `ERROR` after the sweep's own `int(Flag)` bug was fixed (that was the
script, not the library).

## xfail now vs needs a decision

**Pin `xfail(strict=True)` now** (behaviour is clear; the contract test
already states it; the day someone buffers the pipe, the xfail burns):

- `seekable_members=True` → every FILE `seekable() is True` and backward seek
  works, including RAR-via-unrar, 7z AES, zip-aes. Enrol those rows in
  `test_member_stream_contract` (or a corpus-driven twin). Strict xfail on
  the known-piped cases until they actually seek.
- Within-archive disagreement is part of the same pin, not a separate
  product question: if the archive-level flag is on, every FILE honours it.

**Decision first** (either side is a coherent product):

- **`is_encrypted` on the archive.** Keep the docstring (header only: zip is
  the model, RAR/7z folder-or-member bits are wrong) or keep the code (any
  encryption: rewrite the docstring). Do not xfail until that is picked.
- **RAR `listing_cost=INDEXED`.** The enum docstring already special-cases
  it. Either leave the weasel or move RAR to `REQUIRES_SCANNING` to match
  "without scanning header-to-header". Measurement cannot settle it until
  seeks (or bytes read) are visible.
- **ISO `INDEXED` + `member_count=None` + 28 seeks.** Internally consistent
  with "tree lives in the header region" / "counting requires a walk". Only
  a problem if INDEXED is supposed to mean ZIP-like O(1) central directory.

Do not xfail the measurement holes. They are instrumentation gaps.

## What callers already rely on with no capability field

- Branching on `reader.member_streams` (untyped, request-not-delivery).
- "This member's stream is a pipe vs a slice" — only discoverable by
  `seekable()` after `open()`, and that lies relative to the archive flag.
- `unrar` vs direct stored read on RAR (compress method + encryption).
- ZipCrypto vs AES on ZIP (same `ArchiveFormat.ZIP`, different stream
  reality).
- Solid open-order cost (`AccessCost.SOLID`) vs `CONCURRENT` (orthogonal;
  documented, and this run did not contradict it).

## Counts

| Kind | Count (distinct issues, not rows) |
| --- | --- |
| DECLARED-NOT-DELIVERED | 2 (SEEKABLE vs pipe inners; `is_encrypted` vs header-only docstring) |
| DELIVERED-NOT-DECLARED | 0 |
| MEASUREMENT-HOLE | 4 (RAR seeks; dir seeks; compressed-tar listing/SOLID bytes; Path `consumed=None`) |
| UNTYPED | 2 (`member_streams` off the ABC; no per-member stream cap) |
| UNTESTED-NO-CORPUS | 4 (`lzma`, `Z`, `tar.lzma`, `tar.Z`) |
| UNTESTED (skip) | 0 |
| ERROR | 0 |

SEEKABLE DECLARED-NOT-DELIVERED **rows**: 8 (`encrypted`/`encrypted-mixed`/`encrypted-header` 7z; `encrypted`/`encrypted-mixed` rar; `encrypted`/`encrypted-mixed`/`encrypted-multi` zip-aes).

## Did the known RAR SEEKABLE mismatch reproduce on corpus rar?

On **`basic` / `large` rar: no.** Those fixtures are stored; seek works.

On **`encrypted` / `encrypted-mixed` rar: yes.** Encrypted members are
non-seekable pipes under a SEEKABLE flag; mixed disagrees within the archive.

The handbook `tplain.rar` failure was compressed non-solid, not "RAR as a
format". The corpus rar column does not include a compressed non-solid
shape, so a basic-only matrix would have called RAR OK.

## Surprises

- Corpus RAR is stored even at 64 KiB (`large`). The unrar data path is
  effectively encryption-only in this corpus.
- ZipCrypto members **are** seekable under the flag; zip-aes members are
  not. Encryption is not one bucket.
- Unencrypted solid 7z **is** seekable (basic/large). Encrypted 7z is not.
  Same format key, different inner.
- 7z SOLID is the one access-cost claim `io_stats.bytes_decompressed`
  actually confirms. Compressed tar SOLID is not visible that way.
- Lifetime matched the ABC on every format, default and seekable. The
  single-file reader's comment about streams outliving the reader did not
  show up on the public handle.
- `iso` lists 7 members with `member_count is None` while claiming INDEXED.
  Allowed by the None rule; odd next to zip's filled count.
