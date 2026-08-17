# Claims — Topic 8 step 3

Claim inventory for the published guide, grouped by **capability** (not by page).
Verdict column is empty: capability workers fill it after the step-4 checkpoint.

Measured against `main` @ `adb2e3f` (same guide surface as pass 0's `5d08f31` /
commission `d4668c3` — no `docs/` edits since). Guide today: **15 pages, 2 108 lines,
35 `python` blocks**; the sixteenth, `how-it-works.md`, does not exist.

**This document inventories; it does not verify and does not write guide prose.**
No file under `docs/` is edited by this step.

---

## Baseline (step 2)

Recorded 2026-08-17 on this Cloud Agent session. Dependency config: everyday
`[all]` via `uv sync --group dev --extra all` (what `scripts/setup-dev-env.sh` leaves).

### Environment

```text
./scripts/setup-dev-env.sh  → closing verification block:
  ok   unrar: /usr/bin/unrar
  ok   7z: /usr/bin/7z
  ok   benchmark toolchain complete
```

`unrar` 7.00 freeware; `7z` 23.01. Missing either would quietly skip ~109 tests while
the suite still reports green — that did **not** happen here.

### Gates

| Gate | Result |
|---|---|
| `./scripts/check.sh` | all green (ruff check/format, pyrefly, ty, openspec archived + validate, docs nav, docs build) |
| docs nav (inside check) | `docs/: 15 pages, all in nav; repo, site and anchor links all resolve.` |
| `./scripts/test.sh` (`[all]`) | **2453 passed, 23 skipped**, 3 deselected, 6 warnings in ~76s |

23 skips is the provisioned-env range (not the ~167 of an `unrar`/`7z`-less container).
All formats claimed as available below are therefore verifiable in this session unless a
row says otherwise.

### `format_availability()` (every `list_supported_formats()` entry)

Every listed format reports `support=FULL`, `missing=[]` under this install:

| Format | `required_source` |
|---|---|
| `DIRECTORY`, `ISO`, `RAR`, `SEVEN_Z`, `ZIP` | `SEEKABLE` |
| All single-file compressors (`GZ`…`Z`, incl. brotli/lz4/zst/lzip/…) | `FORWARD_ONLY` |
| All TAR / compressed-TAR variants | `FORWARD_ONLY` |

Settle: `src/archivey/internal/registry.py` (`format_availability`, ~193–249, 314–318);
`openspec/specs/packaging-and-extras/spec.md`, `format-detection` / per-format specs.
A claim that "format X works" is **verifiable** here; do not mark such a row
`unverifiable` for missing tooling.

---

## How to read the table

| Column | Meaning |
|---|---|
| **ID** | Stable within this file (`C-<cluster>-NN`). Pre-seeded accuracy rows keep `S-1` / `S-2`. |
| **Claim** | A statement that can be true or false. |
| **Pages** | Every `page:line` that states it — **one row, N pages**. |
| **Settles at** | The `src/` or `openspec/specs/` line that would decide the verdict. |
| **Scope** | Pass-0 routing for the block (`Keep` / `Trim` / `→ DS` / `→ page` / `→ TM` / `Cut`). **Cut → no row.** `→ TM` rows stay, tagged *verify when the threat-model edit is written*. |
| **Verdict** | Left empty for capability workers: `verified` / `wrong` / `unverifiable` (+ reason). |

**Prioritise §A of `brief.md`** (the `fe6d4a7..d4668c3` window) when verifying: extraction
results, diagnostics ceiling, Topic 9 W1–W9, `#225`, terminal escaping, runnable
`python` blocks.

**Cluster order** matches `brief.md` §How to run this.

---

## Pre-seeded accuracy rows (from `scope.md` §Findings)

Do not re-derive. Carry forward.

| ID | Claim | Pages | Settles at | Scope | Verdict |
|---|---|---|---|---|---|
| **S-1** | The nightly-run link target is the current repo name (`davitf/archivey`), not the pre-rename `archivey-2`. **This is O-4** (`review/docs/observations.md`); cite O-4, do not file as new. GitHub redirects, so the URL may still resolve. | `access-and-cost.md:18` | O-4; rename recorded in cutover notes / `CHANGELOG` | Keep (Trim surrounding evidence) | |
| **S-2** | The policy table enumerates all three `ExtractionPolicy` members (`STRICT`, `STANDARD`, `TRUSTED`). Today the table has two rows; `STANDARD` is absent while the page's prose uses `STANDARD` at lines 51, 71, 173, 175. Found independently by both pass-0 agents. | `extracting.md:145-149` (prose uses: `51`, `71`, `173`, `175`) | `src/archivey/internal/extraction_types.py:59-64`; `openspec/specs/safe-extraction/spec.md:377-387` | Keep, fix | |

---

# 1. Opening, detection, sources

Specs: `format-detection`, `archive-reading`, `compressed-streams`.
Primary pages: `opening-and-listing`, `install`, `formats` (detection pointer).

| ID | Claim | Pages | Settles at | Scope | Verdict |
|---|---|---|---|---|---|
| C-open-01 | `open_archive(path)` opens a path, detects format, and iterating the reader yields members in archive order with `name` / `size` / `type`. | `index.md:7-12`; `opening-and-listing.md:8-18`; `philosophy.md:16-20`; `migrating.md:14-15,36-40,71-75` | `src/archivey/core.py` (`open_archive`); `openspec/specs/archive-reading/spec.md` | Keep | |
| C-open-02 | Default open allows any member in any order (random access); non-seekable sources need `streaming=True` or the open fails immediately. | `opening-and-listing.md:20-28`; `access-and-cost.md:141-143`; `philosophy.md:42`; `migrating.md:170-172`; `gotchas.md:25-27` | `openspec/specs/archive-reading/spec.md`; `src/archivey/core.py` | Keep | |
| C-open-03 | `open_archive` on a plain single-file compressor yields a one-member archive named after the file; `open_stream` returns decompressed bytes. | `opening-and-listing.md:32-43`; `formats.md:143`; `migrating.md:20` | `openspec/specs/format-single-file-compressors/spec.md`; `src/archivey/core.py` (`open_stream`) | Keep | |
| C-open-04 | Openable sources: file path, directory path (pseudo-archive), seekable binary stream (any format), non-seekable stream (subset), multi-volume sequence. | `opening-and-listing.md:47-52` | `openspec/specs/archive-reading/spec.md` | Keep | |
| C-open-05 | `format=` that is not a directory, for a path that *is* a directory, raises `ArchiveyUsageError` (does not quietly read the tree). | `opening-and-listing.md:54-55` | `#225` / `src/archivey/core.py`; `openspec/specs/archive-reading/spec.md` | Keep | |
| C-open-06 | A seekable stream is read from current `tell()` as archive byte 0 through EOF; no end bound. | `opening-and-listing.md:57-62` | `openspec/specs/archive-reading/spec.md` (must-explain #12) | Keep | |
| C-open-07 | Non-seekable + `streaming=True` works for TAR (incl. compressed) and single-file compressors; ZIP / 7z / RAR / ISO raise `StreamNotSeekableError` from a pipe. | `opening-and-listing.md:64-68`; `access-and-cost.md:145-146`; `formats.md:10,17` (ZIP/ISO seekable) | `FormatAvailability.required_source` `src/archivey/internal/registry.py:75+`; per-format specs | Keep | |
| C-open-08 | `format_availability(fmt).required_source` is the weakest source shape; `StreamCapability` is ordered `FORWARD_ONLY < SEEKABLE`, so `<=` compares strength. Same comparison works vs `reader.cost.stream_capability`. | `opening-and-listing.md:70-85`; `access-and-cost.md:48-53`; `install.md` (inbound, not yet written) | `src/archivey/cost.py:48+`; `src/archivey/internal/registry.py:75-79`; `openspec/specs/access-mode-and-cost/spec.md` | Keep — canonical home on opening page | |
| C-open-09 | Only 7z and RAR are multi-volume; pass any one volume path and peers are discovered for the named schemes; 7z completeness is checked; old RAR `.rNN` alone is not a set without `.rar`. | `opening-and-listing.md:89-107` | `src/archivey/internal/volumes.py`; `format-7z` / `format-rar` specs | Keep | |
| C-open-10 | Caller-supplied ordered volume sequences skip discovery; one-item sequence = single source; multi-volume for non-7z/RAR raises. | `opening-and-listing.md:103-107` | `src/archivey/internal/volumes.py` | Keep | |
| C-open-11 | `detect_format` reports format + confidence; content wins over filename; disagreement uses bytes and emits `FORMAT_EXTENSION_CONFLICT` naming both candidates. | `opening-and-listing.md:114-123`; `formats.md:224-225` (→ page); `migrating.md:21,109-110` | `src/archivey/diagnostics.py:64`; `openspec/specs/format-detection/spec.md` | Keep — canonical on opening; formats → page | |
| C-open-12 | When a compressor package needed to distinguish `.tar.zst` from `.zst` is missing, detection reports the bare compressor and open raises `UnsupportedFormatError` naming the package. | `opening-and-listing.md:125-130` | `openspec/specs/format-detection/spec.md`; registry / detection | Keep | |
| C-open-13 | `password=` accepts str / list / `PasswordProvider`; most-likely first; wrong candidates cost work. | `opening-and-listing.md:135-141` | `src/archivey/internal/password.py`; `archive-reading` | Trim (shapes/cost stay) | |
| C-open-14 | Password on a format with no encryption is accepted, never consulted, and records `PASSWORD_ARGUMENT_UNUSED` on `reader.diagnostics`. | `opening-and-listing.md:143-148`; `errors-and-diagnostics.md:59` | `src/archivey/diagnostics.py:69`; diagnostics / archive-reading specs | Trim | |
| C-open-15 | Wrong password on an encrypted archive raises `EncryptionError`. | `opening-and-listing.md:150-151`; `errors-and-diagnostics.md:26`; `migrating.md:135` | `src/archivey/exceptions.py`; error-handling | Keep | |
| C-open-16 | `members()` / `scan_members()` raise on terminal damage (never a quietly shortened list); `members_report()` returns recovered members + error; iter / `stream_members` yield prefix then raise. | `opening-and-listing.md:155-162` (Trim to one-liner+link); `errors-and-diagnostics.md:114-132` (canonical); `migrating.md:90-91` | `openspec/specs/error-handling/spec.md`; reader APIs | Keep — canonical on errors; opening Trim | |
| C-open-17 | Duplicate names: last entry has `is_current=True`, earlier `False`; `get` / `open` resolve to last; listings never hide older copies. | `opening-and-listing.md:166-174`; `gotchas.md:28-31`; `extracting.md:161`; `migrating.md:52-54`; `formats.md:120-122` (RAR `-ver`) | `src/archivey/types.py:394`; `openspec/specs/archive-data-model/spec.md`; `safe-extraction` skip non-current | Keep | |
| C-open-18 | Name selectors (`extract_all` / `stream_members` `members=["x"]`) match **every** same-named entry; `extract_all` still skips superseded; `stream_members` yields each version. Pass `ArchiveMember` for identity. | `opening-and-listing.md:182-187`; `gotchas.md:28-31`; `extracting.md:162-163` | `src/archivey/internal/selection.py`; archive-reading / safe-extraction | Keep | |
| C-open-19 | SFX stubs are detected when the archive payload sits behind an executable header. | `formats.md:225-226` | `openspec/specs/format-detection/spec.md` | → page (2 lines stay on formats) | |
| C-open-20 | Code block: `format_availability(detect_format(head)).required_source <= StreamCapability.FORWARD_ONLY` steers pipe vs spool. | `opening-and-listing.md:74-81` | registry + cost enums | Keep | |

**Page coverage in this cluster:** `opening-and-listing` ✓ · `install` (pointer claims in §7) · `formats` detection ✓ · `index` open snippet ✓ · `philosophy` / `migrating` open recipes ✓.

---

# 2. Reading, member lifetime, concurrency

Specs: `archive-reading`, `reader-concurrency`, `archive-data-model`.
Primary pages: `reading-members`, `access-and-cost`, `gotchas`, `support-matrix` (thread table).

| ID | Claim | Pages | Settles at | Scope | Verdict |
|---|---|---|---|---|---|
| C-read-01 | `reader.open(name)` / `reader.read(name)` return member bytes; `read()` has no size limit. | `reading-members.md:9-20`; `index.md:25-28`; `migrating.md:17,39` | `openspec/specs/archive-reading/spec.md` | Keep | |
| C-read-02 | Default: member streams are forward-only (`seek()` raises) unless `seekable_members=True`. | `reading-members.md:24-25`; `gotchas.md:15-18`; `philosophy.md:39-40`; `access-and-cost.md:90-92`; `support-matrix` (via concurrency narrative) | `openspec/specs/reader-concurrency/spec.md`; `archive-reading` | Keep | |
| C-read-03 | Default: one live member stream; a second overlapping `open()` raises `ConcurrentAccessError` unless `concurrent_members=True`. | `reading-members.md:26-28`; `access-and-cost.md:127-128`; `gotchas` (solid/concurrent notes); `philosophy.md:39-41`; `support-matrix.md:112-126` (Trim); `migrating.md:164-166` | `src/archivey/exceptions.py:259`; `openspec/specs/reader-concurrency/spec.md` | Keep; support-matrix Trim / → page for usage-error half | |
| C-read-04 | `ConcurrentAccessError` is an `ArchiveyUsageError`, outside `ArchiveyError`. | `errors-and-diagnostics.md:33-39`; `access-and-cost.md:128`; `support-matrix.md:128-132` (→ page) | `src/archivey/exceptions.py:237,259` | Keep on errors; → page from support-matrix | |
| C-read-05 | `stream_members()` walks archive order; directories/symlinks/hardlinks yield `(member, None)`. | `reading-members.md:47-53,72-73`; `index.md:30-35` | `openspec/specs/archive-reading/spec.md` | Keep | |
| C-read-06 | Stream from `stream_members` is only valid until the iterator advances (closed before next pair). | `reading-members.md:69-71` | archive-reading (must-explain #10) | Keep | |
| C-read-07 | Nothing is decompressed until you read; skipped members never open / never request password — so iterating without reading does not prove the password. Applies to **data** encryption, not header-encrypted 7z/RAR. | `reading-members.md:74-84` | archive-reading; `format-7z` / `format-rar` | Keep; Trim rebuttal tone | |
| C-read-08 | Header-encrypted 7z/RAR need the password at `open_archive()`; without it, `EncryptionError` before any member. | `reading-members.md:79-84`; `formats.md:105-106` (7z empty header → EncryptionError) | format-7z / format-rar | Keep | |
| C-read-09 | `reader.open()` follows symlinks/hardlinks; `stream_members()` does not (yields `None`); broken link → `LinkTargetNotFoundError`; cycles raise. | `reading-members.md:86-93,130-133` | archive-reading / archive-data-model | Keep | |
| C-read-10 | `open()` on a directory/non-file raises `ArchiveyUsageError` naming the type. | `reading-members.md:135-137` | archive-reading | Keep | |
| C-read-11 | `ArchiveMember` from another reader raises `ArchiveyUsageError`; `member in reader` is identity; a string raises `TypeError` pointing at `get`. | `reading-members.md:139-144` | archive-reading | Keep | |
| C-read-12 | Closing the reader closes open member streams; reading afterwards raises. | `reading-members.md:146-156`; `support-matrix.md:134-139,149` | archive-reading; reader-concurrency | Keep | |
| C-read-13 | `streaming=True`: `members`/`get`/`open`/`read` raise `UnsupportedOperationError`; only `__iter__` / `stream_members` / `extract_all`; first of those consumes the pass (incl. after `break`). | `reading-members.md:166-170`; `access-and-cost.md:148-152`; `gotchas.md:25-27`; `index.md:37-40` | archive-reading | Keep; access Trim to drain note | |
| C-read-14 | After partial streaming pass, `scan_members()` finishes/drains when a full list is needed. | `access-and-cost.md:152` | archive-reading | Trim (unique claim kept) | |
| C-read-15 | `streaming=True` cannot combine with `concurrent_members=True`. | `access-and-cost.md:137` | reader-concurrency | Keep | |
| C-read-16 | After materialization, workers may `open()` different members concurrently; same-stream needs caller sync; reader-wide passes stay single-owner. | `access-and-cost.md:134-136`; `support-matrix.md:44-54,104-106,141-150` | reader-concurrency; support-matrix Keep (thread table) | Keep | |
| C-read-17 | `close()` on one thread can block on I/O finishing elsewhere under concurrency. | `support-matrix.md:134-139` | reader-concurrency | Keep | |
| C-read-18 | Thread-safety table rows (different streams yes / same stream no / materialize single-owner / extract+stream_members single-owner / close not race-safe / separate readers yes). | `support-matrix.md:143-150` | reader-concurrency; CI free-threaded job | Keep | |
| C-read-19 | Code block: nested `with open_archive` / `open` / chunked `read` patterns as shown. | `reading-members.md:9-15,30-37,47-53,114-122,152-156,160-164` | runnable against archive-reading | Keep / → page for chunked dup | |
| C-read-20 | Chunked loop recipe (deliver all readable bytes then raise) — byte-identical on two pages; errors page is canonical home. | `reading-members.md:114-126` (→ page); `errors-and-diagnostics.md:167-175` (canonical) | error-handling integrity guarantee | → page / Keep canonical | |

---

# 3. Extraction, policies, results

Specs: `safe-extraction`.
Primary pages: `extracting`, `index`, `cli`, `gotchas`, `migrating`.

| ID | Claim | Pages | Settles at | Scope | Verdict |
|---|---|---|---|---|---|
| C-ext-01 | `archivey.extract(src, dest)` uses `policy=STRICT`, `overwrite=ERROR`, `on_error=STOP` by default (library). | `extracting.md:7-9`; `index.md:21-23`; `reading-members.md:174-175`; `philosophy.md:27-28`; `migrating.md:18-19` | `openspec/specs/safe-extraction/spec.md:31-44`; `src/archivey/internal/extraction_types.py:59` | Keep | |
| C-ext-02 | Safety is opt-out, not opt-in: path traversal, symlink escapes, bombs blocked unless opted out. | `extracting.md:1-3`; `index.md:21-22,56-57`; `philosophy.md:27-28`; `migrating.md:6-8,161-163` | safe-extraction | Keep | |
| C-ext-03 | Archive is untrusted in every byte; an earlier extracted member is untrusted input to later ones (symlink re-resolve). | `extracting.md:14-20` | safe-extraction; threat-model | Trim (~3 lines stay; rest → TM) | |
| C-ext-04 | Local process / other processes trusted; concurrent hostile dest modification out of scope (`O_NOFOLLOW`/`openat` future). | `extracting.md:21-24` | threat-model | → TM (*verify when TM edit written*) | |
| C-ext-05 | Optional deps / `unrar` trusted as code but not for robustness — failures must surface as translated errors. | `extracting.md:25-27` | error-handling; threat-model | → TM (*verify when TM edit written*) | |
| C-ext-06 | Path traversal (`..`, absolute, drive, UNC, NUL) rejected before write; destination containment checked. | `extracting.md:31-33` | `src/archivey/internal/filters.py`; safe-extraction | Trim → one clause | |
| C-ext-07 | File member whose normalized name is `.` or `""` rejected (`PathTraversalError`); only directory may name extraction root. | `extracting.md:34-37` | `filters.py` `check_universal`; safe-extraction | → TM (*verify when TM edit written*) | |
| C-ext-08 | Symlink escapes: lexical + parent-dir + post-`os.symlink` re-resolution; escaping links removed/rejected. | `extracting.md:38-41` | safe-extraction symlink requirements | → TM (*verify when TM edit written*) | |
| C-ext-09 | Hardlink targets containment-checked and resolved positionally to an earlier same-named member by `member_id`. | `extracting.md:42-43,164-165` | safe-extraction hardlink requirements | → TM for layers; identity rule Keep at 164-165 | |
| C-ext-10 | Never write through a symlink; atomic temp + `os.replace`; interrupted extract never leaves half-written dest. | `extracting.md:44-46` | safe-extraction | Trim → one clause | |
| C-ext-11 | Special files always rejected; NTFS junctions detected/flagged/never traversed. | `extracting.md:47-48` | safe-extraction | Trim → one clause | |
| C-ext-12 | Bidi **override/isolate** (U+202A–202E, U+2066–2069) rejected under `STRICT` and `STANDARD` with `DeceptiveNameError`; directional marks (U+061C, U+200E, U+200F) not rejected; RTL script fine; listing/reading emit `MEMBER_NAME_BIDI_CONTROL`. | `extracting.md:49-57`; `errors-and-diagnostics.md:61` | `src/archivey/exceptions.py:174`; `diagnostics.py:63`; safe-extraction bidi requirement; ADR 0017 | Keep, shorter | |
| C-ext-13 | `TRUSTED` lifts bidi rejection and extracts under stored name; universal path safety still on. | `extracting.md:59-65,148,178`; `philosophy.md:54` | `extraction_types.py:48-56` (`ExtractionPolicy.__doc__`); safe-extraction | → DS + one line | |
| C-ext-14 | Bomb guards: cumulative output, per-member ratio, archive-wide static ratio, live ratio for unknown-size/pipes, entry count — halt even under `OnError.CONTINUE`. | `extracting.md:66-68,176,185-206`; `gotchas.md:37-44` | safe-extraction bomb requirements | Trim → one clause; Limits Keep | |
| C-ext-15 | setuid/setgid/sticky stripped except under `TRUSTED`; ownership applied only under `TRUSTED` as root. | `extracting.md:69-70,148` | safe-extraction policy metadata table | Trim → one clause | |
| C-ext-16 | Cross-platform name safety under STRICT/STANDARD: casefold+NFC collisions, reserved names/`:` rejected, trailing-dot/space strip (STRICT), non-UTF-8 percent-escape, `OverwritePolicy.RENAME`. | `extracting.md:71-73,172-175`; `gotchas.md:33-36` | safe-extraction O2/O3/O7; ADR 0013 | → TM for ADR/PR essay; consequences Keep | |
| C-ext-17 | Staging files `.archivey-tmp-*` inside dest; leftover only after hard kill; safe to delete. | `extracting.md:80-84,181` | `src/archivey/internal/extraction.py` | Keep | |
| C-ext-18 | `OnError` governs failures only; policy **blocks** always recorded as `BLOCKED` and continued under STOP or CONTINUE. | `extracting.md:108-111,177`; `cli.md:20-22,28-29` | `extraction_types.py:83-95`; safe-extraction OnError / abort | Keep | |
| C-ext-19 | `abort_on` independent of `OnError`; three members `BLOCKED_MEMBER` / `NAME_COLLISION` / `NAME_SANITIZED` with the documented raises. | `extracting.md:113-128` | `extraction_types.py:98-128`; safe-extraction abort-on-event | Keep existence+example; table → DS | |
| C-ext-20 | Abort is immediate: no later members, **no report returned**; earlier writes stay on disk. | `extracting.md:130-133` | `extraction_types.py:105-108`; safe-extraction | Keep, one line | |
| C-ext-21 | `NAME_COLLISION` fires on every non-TRUSTED collision regardless of overwrite resolution. | `extracting.md:135-137` | AbortOn comment; safe-extraction | → DS | |
| C-ext-22 | `NAME_SANITIZED` is a narrow escape hatch; no policy/preset implies it; audit via `presented_name`. | `extracting.md:139-143` | AbortOn comment; safe-extraction | → DS | |
| C-ext-23 | Policy intents: `STRICT` = untrusted default; `TRUSTED` = ownership/sticky as root, still no traversal. (**S-2:** table omits `STANDARD`.) | `extracting.md:145-149` | `extraction_types.py:59-64`; safe-extraction:377-387 | Keep, fix (S-2) | |
| C-ext-24 | Selective `reader.extract_all(..., members=[...])` works on an open reader. | `extracting.md:151-155` | safe-extraction per-reader extract | Keep | |
| C-ext-25 | `extract_all` skips `is_current=False` as `ExtractionStatus.SUPERSEDED` (≠ `NOT_OVERWRITTEN`, ≠ `OVERWRITTEN`). | `opening-and-listing.md:176-180`; `extracting.md:166-167`; `formats.md:122` | safe-extraction skip non-current; `extraction_types.py:134-143` | Keep | |
| C-ext-26 | Under STRICT/STANDARD, case/NFC twins collide on **all** platforms; `REPLACE` revises clobbered result to `OVERWRITTEN`; `RENAME` → `photo (1).jpg`. | `extracting.md:173`; `gotchas.md:33-36` | safe-extraction collision determinism | Keep (Need-to-know Trim targets ~6 rows) | |
| C-ext-27 | `ExtractionResult.collided_with` names already-written path on collision; `None` when dest was simply pre-existing on disk. | `extracting.md:174` | `extraction_types.py:217`; safe-extraction | Keep | |
| C-ext-28 | Reserved names / `:` rejected under STRICT/STANDARD on every platform. | `extracting.md:175` | safe-extraction | Keep | |
| C-ext-29 | Symlink-hostile filesystems: archivey does **not** copy target bytes through a symlink (unlike `tarfile`); typed failure or skip. | `extracting.md:180` | safe-extraction symlink fail-safe | Keep | |
| C-ext-30 | Nested archives: recursion caller-driven; bomb tracker **not** nesting-aware (zip-of-zips can amplify level by level). | `extracting.md:182,203-206`; `gotchas.md:41-44` | safe-extraction; threat-model O6 | Keep Limits pointer (Q5: no worked recipe) | |
| C-ext-31 | Bomb guards apply during **extraction**; `ListingLimits` on `members()` / `scan_members()` / extract-prep; `stream_members()` intentionally unguarded. | `extracting.md:183,189-201`; `gotchas.md:37-40`; `reading-members.md:17-20` | safe-extraction bomb scope; `config.py` ListingLimits/ExtractionLimits | Keep | |
| C-ext-32 | `extract()` has no `members=` (open reader + `extract_all`); `extract()` accepts non-seekable sources (auto-streaming) while `open_archive` refuses without `streaming=True`. | `reading-members.md:179-184` | safe-extraction one-shot; archive-reading | Keep | |
| C-ext-33 | `zipfile.extractall` can write escaping symlinks; archivey `extract_all` blocks traversal/symlink escapes as `BLOCKED`. | `migrating.md:45-48` | safe-extraction; compare zipfile behaviour | Keep (safety claim — verify carefully) | |
| C-ext-34 | CLI extract defaults diverge from library: `policy=strict`, but `overwrite=rename`, `on_error=continue` (library: ERROR/STOP). | `cli.md:18-22` (comment inside bash); inbound as own block | `src/archivey/cli/main.py:275-277`; `openspec/specs/cli/spec.md:118-143` | Keep, restructure | |
| C-ext-35 | CLI with no `-d`: multi-entry archive lands in `./<stem>/` (tarbomb-safe). | `cli.md:18-20` | cli spec smart dest | Keep | |
| C-ext-36 | CLI exit `3` = extract completed with ≥1 safety-policy block and no member failure; `1` = failure/abort; `0` success; `2` usage; `≥4` reserved. | `cli.md:21-22,44-47`; `extracting.md:177` | `openspec/specs/cli/spec.md:277+` | Keep | |
| C-ext-37 | Code blocks: one-shot extract, policies kwargs, `abort_on={BLOCKED_MEMBER}`, selective extract. | `extracting.md:7-9,88-105,116-120,152-155`; `index.md:21-23` | safe-extraction signatures | Keep | |

---

# 4. Errors, diagnostics, translation

Specs: `error-handling`, `diagnostics`, `logging`.
Primary pages: `errors-and-diagnostics`, `extracting`, `gotchas`.

| ID | Claim | Pages | Settles at | Scope | Verdict |
|---|---|---|---|---|---|
| C-err-01 | Failures from archive/environment derive from `ArchiveyError`; one `except` covers them. | `errors-and-diagnostics.md:7-18`; `index.md:60-61` | `openspec/specs/error-handling/spec.md`; `src/archivey/exceptions.py` | Keep | |
| C-err-02 | Exception table mapping (OpenError subtypes, EncryptionError, Corruption/Truncated, PackageNotInstalled, FilterRejection subtypes, NameCollision/NameRewritten only with `abort_on`, ResourceLimitError). | `errors-and-diagnostics.md:23-31` | exceptions module + error-handling; coupled to §D | Keep (coupled to §D) | |
| C-err-03 | `ArchiveyUsageError` (incl. `ConcurrentAccessError`) is **outside** `ArchiveyError`; `UnsupportedOperationError` for archive-can't-provide is inside. | `errors-and-diagnostics.md:33-39`; `support-matrix.md:128-132` (→ page) | `exceptions.py:237+`; error-handling | Keep | |
| C-err-04 | Diagnostics are queryable on reader and extraction report — prefer them over logs. | `errors-and-diagnostics.md:43-46`; `gotchas.md:105-107`; `extracting.md:227-228` | `openspec/specs/diagnostics/spec.md` | Keep | |
| C-err-05 | Diagnostic codes table: `EMPTY_ARCHIVE`, `EXTENSION_FORMAT_UNCONFIRMED`, `EXPLICIT_FORMAT_LISTED_EMPTY`, `PASSWORD_ARGUMENT_UNUSED`, `ENCODING_ARGUMENT_UNUSED`, `MEMBER_NAME_BIDI_CONTROL` — meanings as stated. | `errors-and-diagnostics.md:55-61`; `gotchas.md:91-103` (empty listing Trim) | `src/archivey/diagnostics.py:63-69`; diagnostics spec | Keep table; trim cells / gotchas Trim | |
| C-err-06 | An empty listing is a diagnostic, never an error; empty tar is all-zeros / blocking-factor legitimate; Docker/OCI empty layers; `detect_format` refuses zero-filled bytes. | `gotchas.md:91-103`; `errors-and-diagnostics.md:56-58` | diagnostics EMPTY_ARCHIVE; format-detection | Trim gotchas to ~3+link | |
| C-err-07 | Per-member extraction outcomes live **only** on `ExtractionReport.results` — not also as diagnostics. Read `results`, not `report.diagnostics`, for extract outcomes. | `errors-and-diagnostics.md:63-74` | diagnostics admission (#235); ExtractionReport | Trim to ~6 | |
| C-err-08 | `DiagnosticPolicy.strict()` raises on `ARCHIVE_INTEGRITY_CODES`; `pedantic()` raises on everything; five codes outside strict set as listed; `ARCHIVE_INTEGRITY_CODES` exported. | `errors-and-diagnostics.md:85-101` | `src/archivey/diagnostics.py:335+`, `DiagnosticPolicy` ~470 | Keep, tighten | |
| C-err-09 | New diagnostic codes may appear in minor releases; `default=RAISE` is not version-stable; removing a code is breaking. | `errors-and-diagnostics.md:103-106` | diagnostics spec | Keep | |
| C-err-10 | Integrity guarantee: full read verifies stored checksum/tag and raises on mismatch; stop early → nothing checked; errors from `read()` not `close()`. | `errors-and-diagnostics.md:136-142`; `reading-members.md:101-104`; `migrating.md:167-169` | error-handling integrity; ADR 0014 | Keep | |
| C-err-11 | `CorruptionError` vs `TruncatedError` is best-effort; catch `ReadError`. Prefix bytes before error are unverified quality. Full-length return ⇒ checksum matched. | `errors-and-diagnostics.md:146-161` | error-handling | Keep | |
| C-err-12 | `read(member.size)` raises on corruption but returns short buffer **without** exception on truncation; chunked-until-empty raises after delivering prefix. | `errors-and-diagnostics.md:159-198`; `reading-members.md:106-110` | error-handling call×failure matrix | Keep — canonical on errors | |
| C-err-13 | Call × failure matrix rows for declared size 500 truncated after 110 (all seven call shapes). | `errors-and-diagnostics.md:185-193` | error-handling | Keep | |
| C-err-14 | Members with no declared size: `read(n)` cannot self-certify; use `read(-1)` or until `b""`. | `errors-and-diagnostics.md:200-201` | error-handling | Keep | |
| C-err-15 | `VerificationMode.STRICT` verifies whole member before returning any bytes. | `errors-and-diagnostics.md:177-179` | verification / streams verify | Keep | |
| C-err-16 | Codec/library exceptions translated to typed `ArchiveyError`s; genuine I/O / unrecognized propagate; no catch-all. (**§B inbound — not yet stated as the full CONTRIBUTING narrative.**) | `extracting.md:74-75` (→ page); CONTRIBUTING:221-230 (source of truth for inbound) | `openspec/specs/error-handling/spec.md`; CONTRIBUTING boundary | → page to errors; inbound write | |
| C-err-17 | Exception and diagnostic **messages are inert for terminal display** (escape at construction). Specs require it; **no page currently states it** (inbound ~3 lines + CLI note). | *(absent — inbound to `errors-and-diagnostics` + `cli`)* | `openspec/specs/error-handling/spec.md:273+`; `diagnostics/spec.md:384+`; `src/archivey/escaping.py` | Guide inbound (coverage, not TM O9 — O9 closed by #236) | |
| C-err-18 | Archivey is stricter than stdlib about damage (raises/diagnostics where tarfile/gzip often stop quietly). | `gotchas.md:59-61` | format-tar / compressed-streams vs stdlib | Keep | |
| C-err-19 | Random-access extract fail-closes before writing when listing ends in terminal damage; not salvage (`--salvage` reserved). | `errors-and-diagnostics.md:131-132`; `cli.md:48`; `migrating.md:173-174` | error-handling; cli reserved | Keep | |

---

# 5. Formats, codecs, stored digests

Specs: seven `format-*`, `archive-data-model`.
Primary pages: `formats`, `install`, `support-matrix` (extras contact), `gotchas`, `acknowledgements`.

| ID | Claim | Pages | Settles at | Scope | Verdict |
|---|---|---|---|---|---|
| C-fmt-01 | Quick matrix rows (core?/extra, listing, random access, notes) for ZIP, TAR, compressed TAR, directory, single-file, 7z, RAR, ISO, zst, lz4, `.Z` as tabled. | `formats.md:8-20`; `index.md:48-50` | per-format specs + packaging-and-extras | Keep | |
| C-fmt-02 | RAR member **data** needs RARLAB `unrar` on `PATH` (not unrar-free/unar/7z); listing/metadata work without it. | `formats.md:22-23,113-115`; `install.md:20-21,25-28`; `extracting.md:218-220`; `migrating.md:130-132`; `acknowledgements.md:72-73` | `format-rar` spec; registry | Keep | |
| C-fmt-03 | ZIP: stdlib zipfile for CD listing; shared codec layer for data; seekable source required even with `streaming=True`. | `formats.md:31-33` | `format-zip` spec | Keep | |
| C-fmt-04 | Extended ZIP codecs via `[recommended]`: Deflate64/PPMd/Zstd; missing → `PackageNotInstalledError`. | `formats.md:34-37` | format-zip; packaging | Keep | |
| C-fmt-05 | Split ZIP (`.z01`…`.zip`) detected and rejected with `UnsupportedFeatureError`. | `formats.md:38-39` | format-zip | Keep | |
| C-fmt-06 | Unsupported compression methods: listing succeeds; reading raises `UnsupportedFeatureError`. | `formats.md:40-41` | format-zip | Keep | |
| C-fmt-07 | ZIP timestamps: DOS base; NTFS/Extended Timestamp extras override when present. | `formats.md:42` | format-zip | Keep | |
| C-fmt-08 | Unflagged ZIP names: prefer UTF-8 when valid else `zip_unflagged_fallback_encoding` (default `cp437`); inferred UTF-8 emits diagnostic; `encoding=` authoritative. | `formats.md:43-49` | `src/archivey/config.py:167`; zip_reader | Keep | |
| C-fmt-09 | Wrongly-set UTF-8 bit-11 can make whole archive unlistable via stdlib zipfile CD parse. | `formats.md:50-54` | format-zip / zipfile behaviour | Keep 2 lines; Cut roadmap clause | |
| C-fmt-10 | WinZip AES (99 / AE-1/AE-2) via `[recommended]`; AE-2 exposes no `crc32`; without package, listed encrypted but read raises `PackageNotInstalledError`. | `formats.md:56-59`; `migrating.md:50-51` | format-zip; zip_aes | Keep | |
| C-fmt-11 | Compressed TAR is solid for random opens; prefer `stream_members()`. | `formats.md:11-12,64-65`; `access-and-cost.md:64-86`; `reading-members.md:60-65` | format-tar; access-mode-and-cost | Keep | |
| C-fmt-12 | Mid-archive corrupt TAR header after first: stdlib stops quietly; archivey raises `CorruptionError` by default on rejected non-null header (incl. final block in random-access). | `formats.md:69-76`; `gotchas.md:70-73` | `tar_reader.py`; format-tar | Keep, tighten | |
| C-fmt-13 | Trailer-less / cat-joined / boundary truncation → `ARCHIVE_EOF_MARKER_MISSING` warning; `strict_archive_eof=True` escalates to `TruncatedError`. | `formats.md:77-81`; `gotchas.md:70-72,75-79` | `config.py:162`; `diagnostics.py:72`; tar_reader | Keep; gotchas Trim | |
| C-fmt-14 | `strict_archive_eof=True` requires every byte after trailer zero (trailing junk/concat → `CorruptionError`); zero padding OK (tar 10 KiB records); cost O(tail), decompresses compressed tails. | `formats.md:82-87`; `gotchas.md:75-79` | tar_reader strict EOF | Keep; gotchas Trim to 1+link | |
| C-fmt-15 | Truncation inside member data always raises `TruncatedError` during iteration regardless of flag. | `formats.md:88-89` | format-tar | Keep | |
| C-fmt-16 | Streaming caveat: corrupt final header caught in random-access, not in forward-only streaming (surfaces as missing-trailer warning). | `formats.md:90-92`; `gotchas.md:72-73` | format-tar | Keep caveat; Cut roadmap | |
| C-fmt-17 | 7z: native header parse + stdlib common codecs; no py7zr on read path; `[recommended]` adds PPMd/Deflate64/Zstd/Brotli/AES; BCJ2 → `UnsupportedFeatureError`. | `formats.md:96-99`; `index.md:54-55`; `migrating.md:128-129,153-155`; `acknowledgements.md:35` | format-7z | Keep | |
| C-fmt-18 | 7z AES+store/copy with no digest/CRC: wrong password can yield garbage; `DIGEST_UNVERIFIABLE` (`reason="no_integrity_anchor"`). | `formats.md:102-104`; `gotchas.md:62-65` | `diagnostics.py:76`; format-7z | Keep | |
| C-fmt-19 | 7z header-encrypted wrong password decoding to zero file records → `EncryptionError` (never silent empty listing); non-empty plausible wrong header can still parse. | `formats.md:105-106`; `gotchas.md:66-69` | format-7z | Keep | |
| C-fmt-20 | `NumCyclesPower` capped ≤24 or `0x3F`; values 25–62 → `UnsupportedFeatureError`. | `formats.md:107-108` | format-7z | → TM (*verify when TM edit written*) | |
| C-fmt-21 | 7z/RAR writing not shipped; py7zr/rarfile are oracles. | `formats.md:109,125`; `migrating.md:137-140`; `acknowledgements.md:35-36` | archive-writing (unlanded); packaging | Keep | |
| C-fmt-22 | RAR metadata native (1.5–RAR5) without unrar; passwords via bare `-p` + secret on stdin (not argv). | `formats.md:113-115` | format-rar; rar_reader | Keep | |
| C-fmt-23 | BLAKE2sp needs no package (native hashlib); HASHMAC tweaked digests via UnRAR when password available — **not** exposed as plain `member.hashes`. | `formats.md:116-119`; `acknowledgements.md:73-74` | format-rar | Trim (keep actionable half) | |
| C-fmt-24 | RAR `-ver` history: `path;n` with `extra["rar.file_version"]`, `is_current=False`; extract skips non-current. | `formats.md:120-122` | format-rar; archive-data-model | Keep | |
| C-fmt-25 | ISO needs `[recommended]`/`pycdlib` + seekable source; namespace Rock Ridge→Joliet→ISO9660 in `ArchiveInfo.extra["iso.namespace"]`; Mode 1 `.bin` may strip to 2048. | `formats.md:129-133` | format-iso | Keep | |
| C-fmt-26 | `import archivey` patches pycdlib process-globally (hang-safety); other pycdlib users in-process see guarded behaviour. **Landing section currently silent** (gotchas links here) — inbound ~2 lines. | `gotchas.md:87-90`; `formats.md:128-133` (silent today) | format-iso; import side effects | Keep gotchas; formats inbound (scope row 10) | |
| C-fmt-27 | Directory backend: same default stream contract (forward-only, one live stream) until SEEKABLE/CONCURRENT declared. | `formats.md:137-139` | format-directory | Keep | |
| C-fmt-28 | Single-file: one synthetic member; `.gz` may expose `extra["gzip.original_filename"]` (FNAME). | `formats.md:143-144` | format-single-file | Keep | |
| C-fmt-29 | `.gz` trailer CRC as `member.hashes["crc32"]` for single-member seekable/path only (omit multi-member gzip / non-seekable). | `formats.md:145-147,186` | format-single-file | Keep | |
| C-fmt-30 | rapidgzip on seekable bare `.gz`/zlib/raw deflate: truncation detection best-effort; set `use_rapidgzip=OFF` for certainty; does **not** apply to ZIP/7z members (CRC via VerifyingStream). | `formats.md:148-153`; `gotchas.md:80-84` | `openspec/specs/seekable-decompressor-streams/spec.md` (O-2 subject; fixed wording at formats:148) | Keep | |
| C-fmt-31 | `.lz` whole-member CRC whenever source seekable (path or memory; not pipe); `seekable_members` irrelevant; multi-member combined via CRC-combine. | `formats.md:154-160,187` | format-single-file; hashing/combine | Trim to rule | |
| C-fmt-32 | `.bz2`/`.xz`/zlib/brotli/`.Z` have no cheap whole-member stored digest on `member.hashes` (zlib Adler verified but not surfaced). | `formats.md:161-164,188` | format-single-file | Trim to fact | |
| C-fmt-33 | `.Z` truncation best-effort: nonzero leftover bits → `TruncatedError`; zero-leftover cuts silent; CLEAR seek points when seekable declared. | `formats.md:165-168,20`; `gotchas.md:85-86` | unix_compress; format-single-file | Keep | |
| C-fmt-34 | `open_stream` non-seekable unless `seekable=True` (mirrors archive rule). | `formats.md:169-170` | compressed-streams | Keep | |
| C-fmt-35 | Stored digests matrix (ZIP/7z/RAR5/gz/lz keys; tar/dir/others none); values are `bytes`; not computed digests. | `formats.md:172-188` | archive-data-model; HashAlgorithm | Keep | |
| C-fmt-36 | Cheap-dedupe snippet: prefer stored blake2sp/crc32 else compute sha256 while reading. | `formats.md:190-220` | HashAlgorithm / open path | Cut to ~8 (Q6: sole dedupe example — raise floor) | |
| C-fmt-37 | Code block / recipes on formats page run under `[all]` with fixtures. | `formats.md:195-217` | runnable | Cut-to-8 still must run | |

**Baseline note:** all formats in `list_supported_formats()` report `FULL` here — including RAR and ISO — so format-"works" claims are verifiable in this session.

---

# 6. Cost, accelerators, measurement

Specs: `access-mode-and-cost`, `seekable-decompressor-streams`.
Primary pages: `access-and-cost`, `gotchas`, `formats`, `philosophy`.

| ID | Claim | Pages | Settles at | Scope | Verdict |
|---|---|---|---|---|---|
| C-cost-01 | Wall-time bands are aspirational targets, not CI hard-fails; PR gate uses structural invariants. | `access-and-cost.md:7-9`; `philosophy.md:70-72` | benchmarks / VISION perf notes | Keep (Trim evidence) | |
| C-cost-02 | Measured nightly column values as tabled (ZIP read 1.87×, etc.) from run `29992136861` on `archivey-2` URL. (**S-1 / O-4** stale repo name.) | `access-and-cost.md:16-33` | nightly artifact (external); O-4 | Trim; S-1 | |
| C-cost-03 | `reader.cost` fields: `listing_cost`, `access_cost`, `stream_capability`, `solid_block_count` with stated meanings. | `access-and-cost.md:39-44` | `src/archivey/cost.py` `CostReceipt` | → DS | |
| C-cost-04 | Cost never changes legality — only price. | `access-and-cost.md:46` | access-mode-and-cost | Keep | |
| C-cost-05 | `listing_cost` / `access_cost` are **not** ordered (kinds of work, not strengths). | `access-and-cost.md:52-53` | cost enums | Keep | |
| C-cost-06 | RAR reports `listing_cost=INDEXED`: headers walked at open; Quick Open not primary source; `members()` O(1) after open; open cost scales with member count. | `access-and-cost.md:55-62` | format-rar; CostReceipt | Keep, trim internals | |
| C-cost-07 | Solid 7z/RAR/compressed TAR: out-of-order `open()` can re-decode; prefer `stream_members()`; `concurrent_members=True` does not remove solid cost. | `access-and-cost.md:64-86`; `gotchas.md:20-24`; `reading-members.md:55-65`; `philosophy.md:33-35` | access-mode-and-cost | Keep | |
| C-cost-08 | Without `seekable_members=True`, `seekable()` is False and `seek()` raises `io.UnsupportedOperation`. | `access-and-cost.md:90-92`; `gotchas.md:15-16` | seekable-decompressor-streams | Keep | |
| C-cost-09 | With flag: XZ/lzip native indexes; gzip/zlib/raw deflate/bzip2 via `[seekable]` rapidgzip when installed; else rewind may re-decompress from start. | `access-and-cost.md:94-98` | seekable-decompressor-streams | Keep, tighten | |
| C-cost-10 | `STREAM_REWIND_REDECOMPRESSES` fires when rewind discards more than ~1 MiB decoded progress (`REWIND_REDECODE_WARN_BYTES`), not by codec name; fires on every qualifying seek if escalated. | `access-and-cost.md:100-111`; `gotchas.md:15-18` | `src/archivey/config.py:93`; `diagnostics.py:78` | Keep rule; → DS for "every seek" | |
| C-cost-11 | `seekable_members` does not change `members()` reports (xz index / lzip trailer still read). | `access-and-cost.md:113-115` | seekable-decompressor-streams | Keep | |
| C-cost-12 | `use_rapidgzip=AUTO` (default) selects rapidgzip only when seekability declared **and** known compressed size ≥ `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` (1 MiB); `ON` forces; `OFF` disables. | `access-and-cost.md:117-121` | `config.py:76,150` | Keep | |
| C-cost-13 | When rapidgzip package **absent**: `ON` raises `PackageNotInstalledError`; `AUTO` falls back silently. (**must-explain #16 half-open** — page states threshold, not absence behaviour; inbound ~2 lines.) | `access-and-cost.md:117-121` (partial today) | `config.py` AcceleratorMode docs; packaging | Keep + inbound clause | |
| C-cost-14 | Multiple password candidates can force confirmation reads; ZipCrypto STORED wrong-candidate may full-member CRC scan. | `access-and-cost.md:154-159`; `formats.md:55-56` | password_confirm; format-zip | Keep | |
| C-cost-15 | Closing source under live accelerator stream: archivey contains upstream `terminate()` into ordinary exception (`tests/test_accelerator_bug3_trap.py`); still a failed read. | `access-and-cost.md:161-172`; `gotchas.md:45-48` | seekable-decompressor-streams; known-issues | Trim evidence → TM | |
| C-cost-16 | Residual uncontained: some **path**-source truncations/CRC mismatches can still `std::terminate` during worker finalization. | `access-and-cost.md:174-177` | known-issues.md | Keep | |
| C-cost-17 | Accelerators off for untrusted input under hard latency budget (`AcceleratorMode.OFF`); C++ can busy-loop where Python timeouts cannot interrupt. | `extracting.md:210-216`; `gotchas.md:49-52` | extracting Hardening; config AcceleratorMode | Trim (harness evidence → TM) | |
| C-cost-18 | Checklist situation→API rows (hash-all → stream_members; solid → archive order; seek → flag; threads → concurrent; stdin → streaming; just unzip → extract). | `access-and-cost.md:181-188` | access-mode-and-cost | Keep as-is | |
| C-cost-19 | `enable_measurement()` is opt-in and open-scoped; `reader.io_stats()` returns `None` outside it. (**§B inbound — not yet on page.**) | *(absent — inbound ~8 lines)* | `src/archivey/internal/measurement.py:26`; `measurement.py` `IoStats` | Guide inbound; fields → existing IoStats docstring | |
| C-cost-20 | Code blocks: harness command, solid do/avoid, concurrent open. | `access-and-cost.md:12-14,71-82,130-132` | runnable / benchmarks | Keep | |

---

# 7. Packaging and platform

Specs: `packaging-and-extras`.
Primary pages: `install`, `support-matrix`, `migrating`, `acknowledgements`, `index` highlights.

| ID | Claim | Pages | Settles at | Scope | Verdict |
|---|---|---|---|---|---|
| C-pkg-01 | Core is zero-dependency and reads ZIP, TAR, directories, stdlib codecs. | `install.md:3-5,9`; `index.md:52-53`; `acknowledgements.md:61`; `formats.md:8-14` | `pyproject.toml` deps; packaging-and-extras | Keep | |
| C-pkg-02 | Four extras only (`recommended`, `seekable`, `all`, `free-threaded`); no per-format extras. | `install.md:8-18`; `acknowledgements.md:63-71` | packaging-and-extras; pyproject optional-deps | Keep | |
| C-pkg-03 | `[recommended]` = every format/codec that installs everywhere; `[seekable]` = +rapidgzip; `[all]` = both. | `install.md:10-12`; `formats.md:25-26`; `acknowledgements.md:65-68` | packaging-and-extras | Keep | |
| C-pkg-04 | `[recommended]` package set is exactly the acknowledgements table (pyppmd, inflate64, brotli, lz4, pybcj, backports.zstd, cryptography, pycdlib, tqdm). | `acknowledgements.md:65` | `pyproject.toml` | Keep | |
| C-pkg-05 | `[seekable]` = rapidgzip only. | `acknowledgements.md:66` | pyproject | Keep | |
| C-pkg-06 | `[free-threaded]` = GIL-safe subset; on 3.13t excludes cryptography (cffi); includes pycdlib/lz4/tqdm/backports.zstd; cryptography on 3.14+ only. | `install.md:16-18,30-34`; `support-matrix.md:67-78`; `acknowledgements.md:67` | packaging; support-matrix extras table | Keep; install free-threaded section → page fold | |
| C-pkg-07 | Importing an undeclared-FT C extension silently re-enables the GIL. | `support-matrix.md:62-64,80-82` | CPython behaviour + measured table | Keep | |
| C-pkg-08 | Free-threaded extras table truth for rapidgzip/pyppmd/inflate64/brotli = No (re-enable GIL); pycdlib/zstd/lz4/tqdm = Yes on 3.13t. | `support-matrix.md:70-78` | CI free-threaded job; packaging | Keep | |
| C-pkg-09 | `pip install archivey[recommended]` fails on free-threaded 3.13 because of cryptography. | `support-matrix.md:86-88` | packaging / cryptography wheels | Keep | |
| C-pkg-10 | CI proves Python/OS matrix as tabled (Linux 3.11–3.14 all extras; core; lowest; 3.13t; macOS/Windows 3.11+3.14). | `support-matrix.md:12-19` | `.github/workflows/ci.yml` | Keep | |
| C-pkg-11 | Non-CPython interpreters not tested. | `support-matrix.md:29-33` | explicit non-claim | Keep | |
| C-pkg-12 | Free-threaded claim is Linux 3.13t only; macOS/Windows FT not claimed; parallel speedup not claimed; only different-member `open` after materialization claimed. | `support-matrix.md:98-108` | support-matrix / CI | Keep | |
| C-pkg-13 | Minimum-versions leg tests floor of each declared range. | `support-matrix.md:21-23` | CI lowest config | Keep | |
| C-pkg-14 | Archivey requires Python 3.11+; pure Python (no compiled extensions of its own). | `support-matrix.md:9-10` | pyproject requires-python | Keep | |
| C-pkg-15 | CLI ships in base package; progress bars need `tqdm` from `[recommended]`; without tqdm command still runs. | `cli.md:1-3` | packaging; cli | Keep | |
| C-pkg-16 | Adapted/vendored: uncompresspy LZW in core (no `[unix-compress]` extra); rarfile SK/Unicode ported into native parser. | `acknowledgements.md:26-27` | unix_compress.py; rar_parser.py licenses | Keep | |
| C-pkg-17 | Only one accelerator library loaded: rapidgzip covers gzip+bzip2; standalone `indexed_bzip2` deliberately not imported (macOS dual-load heap corruption). | `acknowledgements.md:44-46,54` | seekable-decompressor-streams / known-issues | Keep | |
| C-pkg-18 | zstd decode: stdlib `compression.zstd` on 3.14+, else `backports.zstd` — no permanent third-party when on 3.14+. | `formats.md:18`; `acknowledgements.md:56,65,81-82` | packaging-and-extras | Keep | |
| C-pkg-19 | `format_availability()` FULL/PARTIAL/NONE + `missing` runtime query. (**§B inbound to install — not yet written.**) | *(absent on install — inbound)* | `registry.py` FormatSupport / FormatAvailability | Guide inbound (Q4 also wants extra→formats re-index) | |
| C-pkg-20 | Code blocks: four `pip install` lines. | `install.md:8-13` | packaging | Keep | |

---

# 8. Command line

Specs: `cli`.
Primary page: `cli` (also extraction-default divergence rows in §3).

| ID | Claim | Pages | Settles at | Scope | Verdict |
|---|---|---|---|---|---|
| C-cli-01 | Verbs: bare `archivey <archive>` ≡ list; aliases `l`/`t`/`x`/`info`/`detect`; `--version -v` shows version + format availability. | `cli.md:6-13` | `openspec/specs/cli/spec.md`; `src/archivey/cli/main.py` | Keep | |
| C-cli-02 | Verbs are bare words; dash-prefixed forms like `-x` are not mode selectors. | `cli.md:41` | cli spec | Keep | |
| C-cli-03 | File named like a verb (e.g. `./x`) needs explicit verb: `archivey list ./x`. | `cli.md:42-43` | cli spec | Keep | |
| C-cli-04 | Exit codes 0/1/2/3 as documented; `≥4` reserved. | `cli.md:44-47` | cli spec:277+ | Keep | |
| C-cli-05 | `--salvage`, stdin `-`, `hash`/`create`/`convert` reserved for later. | `cli.md:48` | cli spec / IDEAS | Keep | |
| C-cli-06 | Filters: positionals are includes; `--exclude` subtracts; unmatched includes warn; extract/test exit 1 when nothing matched; list warns but stays 0; sole unmatched dest-looking pattern gets `-d` hint. | `cli.md:32-35` | cli spec | Keep | |
| C-cli-07 | `--stop-on-error` ≈ library `OnError.STOP` for member failures; policy blocks still recorded/skipped; exit 3 if only blocks. | `cli.md:28-30` | cli extract_cmd + safe-extraction | Keep | |
| C-cli-08 | `--policy trusted` selects TRUSTED extraction. | `cli.md:36` | cli main policy arg | Keep | |
| C-cli-09 | Passwords on argv are visible to `ps`. (**Inbound — not yet on page.**) | *(absent)* | cli password handling | Guide inbound | |
| C-cli-10 | CLI prints archive-derived names/messages; escaping at message construction (#236). (**Inbound — not yet on page.**) | *(absent)* | error-handling + diagnostics inert-message; `cli/format.py` escape helpers | Guide inbound | |
| C-cli-11 | Bash demo block commands are valid CLI invocations under the stated defaults. | `cli.md:17-36` | cli spec + main | Keep, restructure | |

---

# Cross-cutting pages that are not a capability home

These pages still have checkable claims; each row is filed in the cluster that owns the behaviour. What remains here is **page-shaped** material that workers must not skip when sweeping "every page".

## `api.md`

| ID | Claim | Pages | Settles at | Scope | Verdict |
|---|---|---|---|---|---|
| C-api-01 | Everything documented on the page is re-exported from top-level `archivey` and listed in `archivey.__all__`. (True of listed entries; **reads as completeness** while 56/87 names have entries — §D / QUESTIONS.) | `api.md:3-5` | `src/archivey/__init__.py` `__all__`; mkdocs `:::` entries | Keep, reword — §D | |
| C-api-02 | The 56 `::: archivey.…` entries resolve to public objects with signatures/docstrings. | `api.md:7-91` | griffe / package exports | Keep | |
| C-api-03 | Diagnostics prose note: structured advisories; see diagnostics capability for lifecycle. | `api.md:40-41` | diagnostics spec | Keep | |

## `philosophy.md`

No unique behavioural claims beyond defaults already inventoried in §2–§3–§6 (`forward-only`, one live stream, `streaming=True` fail-fast, `TRUSTED`/`UNLIMITED` hatches, `reader.cost`, `member.hashes`, aspirational bands). Page coverage: **✓ inventoried via those rows**; scope says no cuts and Topic 7 owns positioning overlap with `index` highlights.

## `index.md`

Recipes and highlights inventoried under opening / extraction / reading / packaging. Frozen blocks (Thirty seconds, Highlights) still need their embedded claims verified via the cluster rows above — no separate behavioural surface.

## `acknowledgements.md`

Package/extra/oracle claims inventoried under §5–§7. Attribution-only sentences are not behavioural claims (scope: no cuts; D-f "impressed" leg is not the test).

## `how-it-works.md` — **does not exist**

| ID | Claim | Pages | Settles at | Scope | Verdict |
|---|---|---|---|---|---|
| C-hiw-00 | Page absent: no checkable claims to inventory. D2 six sections + documentation-spec delta remain §B worklist row 1 (~110 lines). Not a silent omission of this file — recorded. | *(no file)* | `openspec/specs/documentation/spec.md:78-93`; D2 | Guide (not yet written) | n/a — gap is the missing page, not an unchecked claim |

---

## Page coverage checklist

Every published page and the missing sixteenth must appear here. "Inventoried" means every Keep/Trim/→DS/→page/→TM block that carries a checkable claim has ≥1 row (or an explicit non-claim note).

| Page | Lines | Inventoried? | Notes |
|---|---:|---|---|
| `index.md` | 93 | yes | via §1–3,7; frozen recipes |
| `install.md` | 34 | yes | §7; inbound rows C-pkg-19 / Q4 re-index noted as absent |
| `opening-and-listing.md` | 203 | yes | §1 |
| `reading-members.md` | 184 | yes | §2 |
| `gotchas.md` | 107 | yes | spread into §1–6 |
| `extracting.md` | 228 | yes | §3 (+ → TM rows retained) |
| `access-and-cost.md` | 188 | yes | §6 (+ S-1) |
| `formats.md` | 228 | yes | §5 |
| `errors-and-diagnostics.md` | 201 | yes | §4 (+ inbound C-err-16/17) |
| `cli.md` | 48 | yes | §8 (+ inbound C-cli-09/10) |
| `migrating.md` | 174 | yes | cross-filed; §A sweep flag on status names |
| `support-matrix.md` | 152 | yes | §2 + §7 |
| `philosophy.md` | 79 | yes | defaults filed in owning clusters; no orphan behavioural claim |
| `api.md` | 91 | yes | § cross-cutting |
| `acknowledgements.md` | 98 | yes | packaging/format rows |
| `how-it-works.md` | — | **recorded absent** | C-hiw-00 |

No page is silently omitted.

---

## Where suspect claims concentrate (step-4 read)

Hand this to the maintainer with the table; capability workers wait on the steer.

1. **Extraction results / `#235` surface (cluster 3 + 4).** Highest §A density: `ExtractionStatus` family (`SUPERSEDED` / `OVERWRITTEN` / `BLOCKED`), `abort_on`, `results`-not-diagnostics, CLI exit 3, migrating's report shape. Pre-seeded **S-2** (`STANDARD` missing from the policy table) sits here. Pages: `extracting`, `errors-and-diagnostics`, `gotchas`, `cli`, `migrating`, `opening-and-listing` (`is_current` extract skip).

2. **Cost / accelerator honesty (cluster 6).** Stale nightly link (**S-1 / O-4**); rapidgzip AUTO threshold vs package-absent half (must-explain #16); contained vs uncontained accelerator faults; rewind diagnostic threshold. Drift history is O-2's home — verify against `seekable-decompressor-streams` / `access-mode-and-cost`, never neighbour pages.

3. **Format residuals that the digest amplifies (cluster 5 + gotchas).** 7z password garbage / header residual, TAR `strict_archive_eof`, rapidgzip bare-stream caveat, `.Z` truncation, empty-listing essay, **silent ISO pycdlib landing**. Several are "Archivey cannot fully fail loudly" admissions — safety-adjacent if overstated.

4. **Absent-but-specified contracts (inbound rows).** Not wrong prose — missing prose that specs already require: terminal-inert messages (#236), error-translation narrative (CONTRIBUTING boundary), `format_availability` support levels on install, measurement/`IoStats`, CLI password-on-argv + terminal-safe note. Workers verifying clusters 4/7/8 should confirm the *spec* side even though the guide row is still blank.

5. **Low suspicion / leave alone until later passes.** `philosophy.md` and `acknowledgements.md` (pass 0: net-zero); `api.md` pending §D shape timing (Q3) rather than accuracy of the `:::` lines themselves.

**Baseline implication:** this session can verify format-availability claims (all `FULL`). Do not convert a green format claim into `unverifiable` here.

---

## What this step did not do

- No verdict filled (except n/a on C-hiw-00).
- No guide prose written; no `docs/` edit.
- No library defect fixed; none newly filed beyond carrying S-1/S-2.
- No capability fan-out — that waits on the step-4 checkpoint steer.
