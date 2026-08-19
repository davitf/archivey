# B. Reading, member lifetime, concurrency

Specs: `archive-reading`, `reader-concurrency`, `archive-data-model`.
Pages: `reading-members`, `access-and-cost`, `gotchas`, `support-matrix`,
`opening-and-listing`, `migrating`, `philosophy`.

| # | Claim | Stated at | Settles it | Ruling | V |
|---|---|---|---|---|---|
| B-1 | `reader.open(name)` as a context manager and `reader.read(name)` are the same thing, one in two calls and one in one | `reading-members.md:9-15`, `migrating.md:17` | `archive-reading:435` | Keep | |
| B-2 | `[code]` the read-a-member block runs | `reading-members.md:9-15` | — (executable) | Keep | |
| B-3 | **`read()` has no size limit** — it returns the whole member however large | `reading-members.md:17-19`, `gotchas.md:37-39` | `archive-reading:435` | Keep | |
| B-4 | Member streams are **forward-only by default**; `seek()` raises unless opened with `seekable_members=True` | `reading-members.md:24-26`, `access-and-cost.md:90-92`, `philosophy.md:39-40`, `gotchas.md:15-16` | `archive-reading:93`, `seekable-decompressor-streams:20` | Keep | |
| B-5 | Specifically, without the flag a member stream reports **`seekable() is False`** and `seek()` raises **`io.UnsupportedOperation`** | `access-and-cost.md:90-91` | `archive-reading:93` | Keep, tighten | |
| B-6 | **One live member stream at a time by default**; a second overlapping `open()` raises `ConcurrentAccessError` unless `concurrent_members=True` | `reading-members.md:26-28`, `access-and-cost.md:127-128`, `support-matrix.md:112-115`, `philosophy.md:39`, `migrating.md:164-166` | `reader-concurrency:22` | Keep (`support-matrix.md:110-127` → `Trim to ~4 + links` as the fourth copy) | |
| B-7 | `[code]` the two-flag `open_archive` block runs | `reading-members.md:30-37` | — (executable) | Keep | |
| B-8 | **Neither flag is free** | `reading-members.md:39` | `access-mode-and-cost:233`, `:265` | Keep | |
| B-9 | `reader.stream_members()` walks the archive **in order**, yielding `(member, stream)` | `reading-members.md:43-45`, `index.md:31-35` | `archive-reading:492` | Keep | |
| B-10 | `[code]` the `stream_members` loop with the `stream is None` guard runs | `reading-members.md:47-53`, `index.md:31-35` | — (executable) | Keep | |
| B-11 | `reader.cost.access_cost` tells you which of the two reading strategies you are in | `reading-members.md:55-56` | `access-mode-and-cost:151` | Keep | |
| B-12 | For **ZIP or uncompressed TAR** `access_cost` is `DIRECT`: members are stored independently, so one costs the same as all | `reading-members.md:56-58`, `formats.md:10-11`, `access-and-cost.md:42` | `access-mode-and-cost:151`, `format-zip:20`, `format-tar:20` | Keep | |
| B-13 | For **solid 7z/RAR or any compressed tar** it is `SOLID`: reading a middle member decompresses everything before it, and per-member opens turn a linear read quadratic | `reading-members.md:60-63`, `access-and-cost.md:66-67`, `gotchas.md:20-23`, `formats.md:11-12`, `migrating.md:86-88`, `philosophy.md:33-34` | `access-mode-and-cost:151`, `format-7z:243`, `format-rar:168` | Keep | |
| B-14 | **Nothing warns you about the solid-open cost** — it is slow, not wrong; check `access_cost` instead | `reading-members.md:63-65` | `access-mode-and-cost:151` | Keep | |
| B-15 | **A yielded stream is valid only until you advance**: the iterator closes it before producing the next pair | `reading-members.md:69-71` | `archive-reading:492` | Keep | |
| B-16 | **Non-file members yield `None`** — directories, symlinks and hardlinks all come through as `(member, None)` | `reading-members.md:72-73`, `reading-members.md:132-133` | `archive-reading:480` | Keep | |
| B-17 | **Nothing is decompressed until you read**: a skipped member is never opened and no password is requested for it, so "I iterated without error" does not prove the password | `reading-members.md:74-77` | `archive-reading:492`, `archive-reading:632` | Keep | |
| B-18 | B-17 applies to **data** encryption only; **header**-encrypted 7z and RAR need the password at `open_archive()` and raise `EncryptionError` before any member exists | `reading-members.md:79-84`, `formats.md:106`, `formats.md:117` | `format-7z:197`, `format-rar:308` | Trim | |
| B-19 | `reader.open()` **follows links**; `stream_members()` deliberately does not, so a loop that skips `None` skips links | `reading-members.md:86-93`, `reading-members.md:130-133` | `archive-reading:539` | Keep | |
| B-20 | Following a link means reading the target's bytes, which in a single forward pass may already be behind you — formats that *could* reach it follow the same rule so loop shape does not vary | `reading-members.md:87-90` | `archive-reading:539` | Keep | |
| B-21 | `member.link_target` lets you resolve links yourself | `reading-members.md:92-93` | `archive-data-model:122` | Keep | |
| B-22 | A `stream_members()` pass **owns the reader**: `open()`, `members()` or another pass inside the loop raises `ArchiveyUsageError` | `reading-members.md:95-97`, `support-matrix.md:104-106` | `reader-concurrency:192`, `access-mode-and-cost:120` | Keep | |
| B-23 | Reading a member **to its end** verifies it wherever the archive stores a checksum, and raises rather than handing over short or wrong data | `reading-members.md:101-102`, `errors-and-diagnostics.md:136-138`, `index.md:25-26` | `compressed-streams:254`, `archive-reading:435` | Keep | |
| B-24 | **A broken link raises `LinkTargetNotFoundError`; a cycle raises rather than spinning** | `reading-members.md:131-132` | `archive-reading:539`, `src/archivey/exceptions.py:137` | Keep | |
| B-25 | `reader.open()` on a **directory or other non-file entry** raises `ArchiveyUsageError` naming the type | `reading-members.md:135-137` | `archive-reading:435` | Keep | |
| B-26 | **A member belongs to the reader that produced it** — passing an `ArchiveMember` from another archive raises `ArchiveyUsageError` rather than resolving it against the wrong offsets | `reading-members.md:139-141` | `archive-reading:406` | Keep | |
| B-27 | `member in reader` tests **identity, not name**; a string raises `TypeError` and points at `reader.get(name)` | `reading-members.md:141-144` | `archive-reading:406` | Keep | |
| B-28 | **A member stream does not outlive its reader**: closing the reader closes open member streams, matching `ZipFile.close()` / `TarFile.close()` | `reading-members.md:146-150`, `support-matrix.md:136-137` | `archive-reading:581`, `reader-concurrency:166` | Keep | |
| B-29 | `[code]` the nested-`with` block runs | `reading-members.md:152-156` | — (executable) | Keep | |
| B-30 | Under `streaming=True` the random-access methods — `members()`, `get()`, `open()`, `read()` — raise **`UnsupportedOperationError`** | `reading-members.md:166-168` | `access-mode-and-cost:50`, `access-mode-and-cost:120` | Keep | |
| B-31 | What remains is `__iter__`, `stream_members()` and `extract_all()`, and **you get one of them**: the first consumes the source, even after an early `break` | `reading-members.md:168-170`, `access-and-cost.md:150-152`, `gotchas.md:25-26` | `access-mode-and-cost:50` | Keep (`access-and-cost.md:148-152` → `Trim to 2 + link`, third copy) | |
| B-32 | `scan_members()` is how you drain/finish for a full list after a partial pass | `access-and-cost.md:152` | `access-mode-and-cost:85`, `archive-reading:339` | Trim (the one unique claim of the block) | |
| B-33 | `[code]` the streaming-mode pipe block runs | `reading-members.md:160-164` | — (executable) | Keep | |
| B-34 | **`archivey.extract(src, dest)` has no `members=` argument** — selecting a subset needs `reader.extract_all(members=...)` | `reading-members.md:179-181` | `safe-extraction:21`, `safe-extraction:65` | Keep | |
| B-35 | **`extract()` accepts a non-seekable source**, opening it in streaming mode for you, where `open_archive` refuses one without `streaming=True` | `reading-members.md:182-184` | `safe-extraction:21` | Keep | |
| B-36 | After materialization, workers may `open()` **different** members concurrently; same-stream access still needs caller synchronization | `access-and-cost.md:134-135`, `support-matrix.md:44-46`, `support-matrix.md:145-146` | `reader-concurrency:22`, `reader-concurrency:149` | Keep | |
| B-37 | Reader-wide passes (`__iter__` / `stream_members` / `extract_all`) remain **single-owner** | `access-and-cost.md:135-136`, `support-matrix.md:104-106`, `support-matrix.md:147-148` | `reader-concurrency:192` | Keep | |
| B-38 | **`streaming=True` cannot combine with `concurrent_members=True`** | `access-and-cost.md:137` | `access-mode-and-cost:233` | Keep | |
| B-39 | `[code]` the one-line `open_archive(src, concurrent_members=True)` block runs | `access-and-cost.md:130-132` | — (executable) | Keep | |
| B-40 | `close()` is **safe to call twice** and **not safe to race** against in-flight opens; it can block on I/O finishing elsewhere | `support-matrix.md:136-139`, `support-matrix.md:149` | `reader-concurrency:166`, `reader-concurrency:266` | Keep | |
| B-41 | **Separate `ArchiveReader` objects share no mutable state** and are safe across threads | `support-matrix.md:150` | `reader-concurrency:22` | Keep | |
| B-42 | The single-stream default exists so a reader can **hold one decode position per archive**, which is the cheap path for every format | `support-matrix.md:124-126` | `reader-concurrency:22`, `access-mode-and-cost:233` | `Trim to ~4 + links` — this sentence is the block's unique claim | |
| B-43 | `[code]` the fail-fast `ConcurrentAccessError` demo runs and raises where the comment says | `support-matrix.md:117-121` | — (executable) | `Trim to ~4 + links` | |
| B-44 | `[code]` the free-threading fan-out example runs | `support-matrix.md:48-54` | — (executable) | Keep, tighten to ~12 | |
| B-45 | `stdlib` peers behave differently on three points archivey inverts: `extractfile` can return `None` (archivey raises typed), `tarfile` re-decompresses silently (archivey exposes cost), `tarfile` stops silently on truncation (archivey gives prefix + error) | `migrating.md:84-91` | `format-tar:125`, `archive-reading:199` | Keep | |
| B-46 | **`read()` is all-or-raise** for migrators: a truncated member raises rather than returning a short body; a chunked loop gets the recoverable prefix | `migrating.md:167-169` | `compressed-streams:155`, `archive-reading:435` | Keep | |
| B-47 | Format differences surface **as data** (`None`, documented sentinels, cost receipts), never as a different API per backend | `philosophy.md:22-23` | `archive-data-model:21`, `archive-reading:20` | Keep | |
| B-48 | `[code]` the `tarfile` before/after pair runs, and `reader.read("etc/config")` is the stated equivalent of `tf.extractfile(...).read()` | `migrating.md:59-76` | — (executable) | Keep | |

## B — problems and gaps met while extracting

- **B-6 is stated on five pages.** `scope.md` counted four (`reading-members`,
  `access-and-cost`, `support-matrix`, `philosophy`); `migrating.md:164-166` is a fifth.
  Whatever the trim does to `support-matrix.md`, the migration page's copy has to be
  checked too — it is the one a reader arrives at with stdlib habits.
- The **`stream_members()` link asymmetry** (B-19) is the single most load-bearing
  behaviour in the cluster and is stated in two places on one page. It is not stated on
  `gotchas.md`, which is defensible (the digest cannot hold everything) but is worth a
  deliberate call rather than an accident.

---

