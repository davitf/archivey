# The *expected* column — written from `VISION.md` + `openspec/specs/` alone

> **What this file is.** The brief (§Suggested process step 2, §Known seeds
> counterweight) asks for the matrix's *expected* column to be written **before** the
> probe runs and **without** consulting the seeds or the implementation, so the seed
> list does not decide in advance where to look. This is that column. The `observed`
> column lives in [`parity-matrix.md`](parity-matrix.md); the diff between them is
> where the review's leads came from.
>
> **Contamination disclosure (matters for how much this is worth).** Before writing
> this, I had read `src/archivey/core.py` — the two entry points and their docstrings —
> while orienting. So rows E1–E8 (entry-point admission rules) are **not** independent:
> agreement there is weak evidence and I have weighted it as such. Rows R\*, C\*, P\*,
> H\*, X\* and the error rows were written from `archive-reading`,
> `access-mode-and-cost`, `error-handling`, `archive-data-model`,
> `format-single-file-compressors` and `VISION.md` only, with no backend read. The
> brief's own caveat applies either way: **weight the disagreements heavily and the
> agreements lightly.**

Sources consulted, and nothing else:

- `VISION.md` — "one uniform interface", "no surprises", "hashes without decompression
  where possible", "damaged input is a first-class citizen"
- `openspec/specs/archive-reading/spec.md`
- `openspec/specs/access-mode-and-cost/spec.md`
- `openspec/specs/error-handling/spec.md`
- `openspec/specs/archive-data-model/spec.md` (hashes/equality clauses)
- `openspec/specs/format-single-file-compressors/spec.md` (size + hashes clauses)

Column key for the "same across formats?" column:

- **uniform** — the spec states one rule for every backend; a per-format difference
  would be a finding.
- **format-forced** — the spec itself names a per-format difference, so divergence is
  expected and the question is only whether it is *queryable*.
- **unstated** — no spec clause I could find fixes this. A divergence here is not a
  spec violation; it is a gap, and the review has to decide accident vs law.

---

## E — entry-point admission (weak evidence: see disclosure)

| # | Row | Expected from spec | Same across formats? |
|---|---|---|---|
| E1 | `open_archive(Path)` , `streaming=False` | Opens. | uniform |
| E2 | `open_archive(seekable BinaryIO)`, `streaming=False` | Opens. Source is buffered for full-count `read(n)`; archive starts at current `tell()`. | uniform |
| E3 | `open_archive(non-seekable)`, `streaming=False` | `StreamNotSeekableError` **at open, before member data**. Never a silent degrade to forward-only, never a buffer-to-temp. | uniform |
| E4 | `open_archive(non-seekable)`, `streaming=True` | Opens **iff** the format's index is at the front. Trailing-index formats (ZIP CD, 7z EOF header, ISO) raise `StreamNotSeekableError` naming the reason. | format-forced |
| E5 | `open_archive(streaming=True, concurrent_members=True)` | `ArchiveyUsageError` at open, no reader. | uniform |
| E6 | `open_archive(dir_path, format=ZIP)` | `ArchiveyUsageError` naming the path and the requested format. | uniform (directory only) |
| E7 | `open_archive(..., password=...)` on a format with no encryption | Rejected — `UnsupportedOperationError`. A bare provider callable is fine. | uniform |
| E8 | `open_archive(..., member_streams=...)` | `TypeError` — parameter no longer exists. | uniform |

## R — reader surface, random access (`streaming=False`)

| # | Row | Expected from spec | Same across formats? |
|---|---|---|---|
| R1 | `len(reader)` | Python `TypeError`, **every** format, every mode. | uniform |
| R2 | `"name" in reader` | `TypeError` pointing the caller at `get()`. Never iterates. | uniform |
| R3 | `member in reader` (identity) | `True` for members this reader yielded; `False` for foreign; O(1); no scan. | uniform |
| R4 | `members()` / `scan_members()` | Complete fully-resolved list, or **raise**. Never a partial list. | uniform |
| R5 | `members_report()` | Always a `MemberListReport`; terminal archive-level listing errors go on `.error`, not raised. | uniform |
| R6 | `members_report_if_available()` before any pass | Complete report for leading-index (directory, ISO) **and** trailing-index (ZIP, 7z) backends; `None` for no-index (TAR) — per the index-topology table. | format-forced, and the spec fixes each cell |
| R7 | `get("missing")` | `None` (or the passed default). `open`/`read` of a missing name → `KeyError`. | uniform |
| R8 | `open(foreign_member)` | `ValueError`. | uniform |
| R9 | `open`/`read` of a `DIRECTORY` member | `ArchiveyUsageError`. **Not** empty bytes, **not** a raw `IsADirectoryError`, **not** a format `CorruptionError`. Explicitly listed for ZIP/TAR/ISO/directory/7z. | uniform |
| R10 | `open`/`read` of `ANTI` / `OTHER` | `ArchiveyUsageError`. | uniform |
| R11 | `open`/`read` of a symlink with a missing target | `LinkTargetNotFoundError` (an `ArchiveyError`, *not* a usage error). | uniform |
| R12 | Symlink cycle | `ReadError`, detected by member **id**, no depth limit. | uniform |
| R13 | `stream_members()` non-file members | `stream is None` — never an empty `ArchiveStream`. | uniform |
| R14 | Second overlapping `open()` without `concurrent_members` | `ConcurrentAccessError`, message names `concurrent_members=True` **and** carries the `open_archive()` `file:line`; the first stream stays readable. Listed for ZIP/TAR/ISO/single-file/dir. | uniform |
| R15 | `seek()` on a member stream without `seekable_members` | `io.UnsupportedOperation`; `seekable()` is `False`; `tell()` works. Explicitly *including* a real directory file. | uniform |
| R16 | Random `open()` during an active `stream_members()` pass | `ArchiveyUsageError` at the later op; the pass stays usable. | uniform |

## S — streaming mode (`streaming=True`)

| # | Row | Expected from spec | Same across formats? |
|---|---|---|---|
| S1 | `members()` / `get()` / `open()` / `read()` | `UnsupportedOperationError` **uniformly**, independent of any loaded index. | uniform |
| S2 | Second forward pass (`__iter__`, `stream_members`, `extract_all`) | `UnsupportedOperationError` — "all formats", even after the first completed. | uniform |
| S3 | `scan_members()` after early `break` | Drains the remainder, returns the complete resolved list (or raises). | uniform |
| S4 | `members_report_if_available()` | Never scans, never consumes the pass — in either mode. | uniform |
| S5 | `cost` / `info` / `format` / `close` / context manager | Available in both modes. | uniform |

## C — cost receipt

| # | Row | Expected from spec | Same across formats? |
|---|---|---|---|
| C1 | `ar.cost` populated at open | Yes, "computed during open before heavy I/O", no separate scan or member read. | uniform |
| C2 | ZIP | `INDEXED` + `DIRECT`. | format-forced (named) |
| C3 | plain TAR, file | `REQUIRES_SCANNING` + `DIRECT` + `SEEKABLE`. | format-forced (named) |
| C4 | plain TAR, pipe | Same, but `FORWARD_ONLY`. `access_cost` stays `DIRECT`. | format-forced (named) |
| C5 | `.tar.gz` | `REQUIRES_DECOMPRESSION` + `SOLID`. | format-forced (named) |
| C6 | solid 7z | `INDEXED` + `SOLID`, `solid_block_count` = folder count, `info.is_solid` true. | format-forced (named) |
| C7 | directory, ISO, single-file | **unstated** — the spec's examples stop at the five above. My expectation from the axis definitions: directory → `INDEXED`/`REQUIRES_SCANNING` + `DIRECT`; ISO → `INDEXED` + `DIRECT`; single-file → `REQUIRES_DECOMPRESSION`(size unknown until read) + `DIRECT`, `solid_block_count` `None` or 1. | unstated |
| C8 | `notes` | Static caveats only. Never an occurrence log or counter. | uniform |
| C9 | Runtime rewind / degraded seek index | On **diagnostics**, never on `CostReceipt`. | uniform |

## P — passwords and encryption

| # | Row | Expected from spec | Same across formats? |
|---|---|---|---|
| P1 | Encrypted member, no password | `EncryptionError`. | uniform |
| P2 | Encrypted member, wrong password | `EncryptionError` — **or**, where the format's check is weak and only one candidate exists, an ordinary read-time integrity error. The spec permits the second only for the single-candidate case. | format-forced, bounded |
| P3 | Two candidates, first wrong, weak check | Must **confirm** before accepting; must not return unvalidated bytes. Confirmation cost bounded, not proportional to member size. | uniform |
| P4 | All candidates fail confirmation | An "irreducible ambiguity" report (wrong password **or** corrupt unit). MAY be `EncryptionError`. Never candidate bytes. | uniform |
| P5 | Header-encrypted archive, provider only | Provider called with `member is None`. | format-forced (7z `-mhe`) |
| P6 | Password work timing | **unstated** in the specs I read as a cross-format rule. VISION "no surprises" implies: no work earlier than the caller asked for. | unstated |

## H — hashes, sizes, digests

| # | Row | Expected from spec | Same across formats? |
|---|---|---|---|
| H1 | `member.hashes` | A mapping; stored digests surfaced when readable **without decompressing**, omitted otherwise. Excluded from equality. | format-forced (each format's spec fixes its own row) |
| H2 | ZIP | `CRC32` present. | format-forced (named) |
| H3 | RAR5 | `BLAKE2SP` present. | format-forced (named) |
| H4 | single-file `.gz` / `.lz` | CRC-32 from the trailer, incl. the multi-member combine algebra; `.zz`/zlib **omitted** (no size field for reliable combining). | format-forced (named) |
| H5 | `member.size` for `.gz` | `None` (VISION-consistent honest `None`, not a guess). BZ2/ZLIB/BR/`.Z` → `None` until full decompression. XZ/ZST/LZ4/lzip/LZMA-alone → header/trailer value when the encoder wrote one, else `None`. | format-forced (named) |
| H6 | TAR, 7z, ISO, directory `hashes` | **unstated** as a positive rule. My expectation from "surface what the format stores, omit otherwise": empty for TAR (stores no digest), empty for directory and ISO, present for 7z (CRC32 in the header). | unstated for TAR/ISO/dir, implied for 7z |
| H7 | Full `read()` | Verifies supported digests regardless of what `hashes` exposes; streaming verification raises `CorruptionError` only on the **terminal** read; `read()` raises without returning bytes. | uniform |

## X — errors, close, lifecycle

| # | Row | Expected from spec | Same across formats? |
|---|---|---|---|
| X1 | Every library-detected failure | An `ArchiveyError` subclass from the exact published tree. | uniform |
| X2 | Caller misuse | `ArchiveyUsageError`, **outside** `ArchiveyError`. | uniform |
| X3 | Filesystem `OSError`, `KeyboardInterrupt`, `MemoryError` | Propagate **unchanged**; never reclassified as `CorruptionError`/`TruncatedError`. | uniform |
| X4 | Decoding-library exceptions | Translated, with `__cause__` preserved; `source_format` / `archive_name` / `member_name` stamped centrally by the base reader, not hand-filled per backend. | uniform |
| X5 | Raw `ValueError` / `RuntimeError` / `NotImplementedError` across the public boundary | Not permitted by the tree — anything from a decoding taxonomy is translated; only genuinely unrecognized runtime errors propagate raw. | uniform |
| X6 | Any reader op after `close()` | `ArchiveyUsageError`. Repeated `close()` / `__exit__` are no-ops. | uniform |
| X7 | `reader.close()` with member streams open | Closes them in open order, after the reader has transitioned to closed; backend teardown runs once, after the last. | uniform |
| X8 | Caller-supplied `BinaryIO` | The library never closes it. | uniform |
| X9 | Terminal listing error after a recoverable prefix | `members_report()` returns prefix + `error`; `__iter__` / `stream_members` yield the prefix then raise; `members()` / `scan_members()` raise. **Both access modes.** | uniform (this is the strongest cross-mode claim in the spec set) |
| X10 | Damaged input generally | VISION: "every member that *is* recoverable plus an honest error". The spec set only guarantees this for the **listing** pass (X9); `VISION.md` itself records data-read salvage as a known gap. | uniform for listing; acknowledged gap for reads |

## Rows the specs do not decide at all

Recorded here so the observed column can say "no spec clause governs this" instead of
manufacturing a violation:

1. **`open_stream` vs `open_archive` on the same `.gz` file** — both accept it. No spec
   clause I found says whether `member.size`, `hashes`, diagnostics, or error types must
   agree between the two routes. (`compressed-streams` governs the stream;
   `format-single-file-compressors` governs the member. Nothing joins them.)
2. **`extract()`'s automatic streaming choice** — `core.py`'s docstring documents it;
   no spec requirement I found states it, so no spec says whether the resulting
   behaviour must match an explicit `open_archive(streaming=True)` + `extract_all`.
3. **Cost receipt rows for directory / ISO / single-file** (C7).
4. **`hashes` emptiness for TAR / ISO / directory** (H6).
5. **When password work happens** relative to `open_archive()` returning (P6).
6. **Whether an ignored/no-op argument must be an error** — the directory `format=`
   case is specced (E6); nothing generalizes it to other silently-discarded inputs.

Those six are exactly where I expect the review to have to *decide* rather than
*check* — and where the maintainer, not the reviewer, owns the call.
