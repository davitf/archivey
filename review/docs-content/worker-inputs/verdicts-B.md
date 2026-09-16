# Verdicts — Worker B (Reading, member lifetime, concurrency)

Config for this pass: **`[all]`** (`uv sync --group dev --extra all`), unless a row notes
otherwise. Spec citations preferred over `src/` when both speak (O-26). When two specs
disagree on the same fact, the more specific capability is named in Evidence.
`[code]` rows were executed with `uv run --no-sync` against temp ZIP/TAR fixtures under
`/tmp/b-verify/` plus `tests/fixtures/rar/` / `sevenzip/` where needed.

| # | V | Evidence |
|---|---|---|
| B-1 | verified | `archive-reading` Reading member data: `open()` → `ArchiveStream`, `read()` → full `bytes`. Guide “same thing, in one call” = full-content equivalence. Spot-check: `open`+`stream.read()` and `read()` both returned `b"hello-a"`. |
| B-2 | verified | `[code]` `reading-members.md:9-15` ran on temp `photos.zip` (`subdir/a.txt`). Both paths succeeded; bytes equal. |
| B-3 | verified | `archive-reading` Reading member data: `read()` materializes full payload without extraction bomb checks. Signature `read(self, member: str \| ArchiveMember) -> bytes` — no size argument. Matches gotchas unbounded-`read()` warning. |
| B-4 | verified | `archive-reading` Declared member-stream capabilities (default forward-only); `seekable-decompressor-streams` Seek machinery demand-driven. Spot-check: without flag `seek()` → `io.UnsupportedOperation`; with `seekable_members=True`, `seekable()` true and seek works on ZIP member. |
| B-5 | verified | Same requirement: streams report `seekable() is False`; `seek()` → `io.UnsupportedOperation`. Spot-check: `(False, io.UnsupportedOperation)` on default ZIP member stream. |
| B-6 | verified | `reader-concurrency` Multiple concurrently-open member streams + `archive-reading` default one live stream → `ConcurrentAccessError`. Spot-check: overlapping second `open()` raised `ConcurrentAccessError` naming `concurrent_members=True`. Stated on five pages (incl. `migrating.md:164-166`) — duplication, not a falsehood. |
| B-7 | verified | `[code]` `reading-members.md:30-37` two-flag `open_archive` ran; both members openable concurrently; with `seekable_members=True`, stream reported seekable. |
| B-8 | verified | `access-mode-and-cost` Declared capabilities compose + Concurrent-stream cost informational; seek path costs in `seekable-decompressor-streams`. Neither flag is a free no-op: concurrent keeps solid open-order cost; seek may build indexes / re-decompress. |
| B-9 | verified | `archive-reading` Bounded-memory sequential streaming via `stream_members`: yields `(member, stream)` in archive order. Spot-check on `backup.tar.gz`: order `etc/config`, `readme.txt`, `etc/`, `link-to-readme`. |
| B-10 | verified | `[code]` `reading-members.md:47-53` / `index.md` loop with `stream is None` guard ran; directories/symlink skipped; file members readable. |
| B-11 | verified | `access-mode-and-cost` Exposing a CostReceipt: `ar.cost.access_cost` is `DIRECT` \| `SOLID`. Spot-check: attribute present immediately after open. |
| B-12 | verified | `format-zip` / `format-tar` Report properties: ZIP and plain TAR → `AccessCost.DIRECT`. Spot-check: ZIP and `plain.tar` both `DIRECT`. |
| B-13 | verified | `access-mode-and-cost` cost matrix; `format-tar` compressed → `SOLID`; `format-7z` / `format-rar` solid → `SOLID`. Spot-check: `.tar.gz` `SOLID`; `basic_solid__.rar` `SOLID`; `basic_nonsolid__.rar` `DIRECT`; `lz4.7z` `SOLID`. |
| B-14 | verified | `access-mode-and-cost` Concurrent-stream cost / solid open-order: **no** diagnostic and **no** `warnings.warn`; signal is `cost.access_cost == SOLID` at open. Matches “slow, not wrong.” |
| B-15 | verified | `archive-reading` `stream_members`: iterator closes/invalidates prior stream before next yield. Spot-check: after advance, prior stream `read()` → `ValueError: I/O operation on closed file.` |
| B-16 | verified | `archive-reading` Non-file `stream_members` yield None (`DIRECTORY`, `SYMLINK`, `HARDLINK`, …). Spot-check: dir + symlink → `None`; separate hardlink TAR → `HARDLINK` with `stream is None`. |
| B-17 | verified | `archive-reading` `stream_members`: unselected/unread not opened; no password request. Spot-check: `encryption__.rar` listed without password; full `stream_members` skip without error; reading a stream → `EncryptionError`. |
| B-18 | verified | `format-7z` / `format-rar` header-encryption matrices: no password → `EncryptionError` before listing. Spot-check: `encrypted_header__.rar` → `EncryptionError` … encrypted headers … no password. (Stated-at `formats.md:117` is BLAKE2sp prose — weak cite, claim still holds via reading-members + specs.) |
| B-19 | verified | `archive-reading` Transparent link following (`open`/`read` follow) + non-file `stream_members` → `None`. Spot-check: `read("link-to-readme")` → `b"readme"`; `stream_members` yields `(SYMLINK, None)`. |
| B-20 | verified | Same link + `stream_members` rules: links always `None` in the forward pass so loop shape does not vary by format reachability. Spec requires uniform non-file `None`, not per-format follow-in-pass. |
| B-21 | verified | `archive-data-model` ArchiveMember complete record includes `link_target`. Spot-check: symlink member `link_target == "readme.txt"`. |
| B-22 | verified | `reader-concurrency` Distinct passes remain single-owner; `access-mode-and-cost` summary. Spot-check: during `stream_members`, `open()` / `members()` / nested `stream_members` → `ArchiveyUsageError`. |
| B-23 | verified | `compressed-streams` Decompressed output digests verified at clean EOF; `archive-reading` full `read()` raises without returning bytes on digest fault. Spot-check: bad ZIP CRC → `CorruptionError` from `read()`; chunked path delivered prefix then raised. (Same guide page also documents sized `read(n)` short-on-truncation — harvest, not a false integrity claim.) |
| B-24 | verified | `archive-reading` Transparent link following: missing → `LinkTargetNotFoundError`; cycle → `ReadError`. Spot-check: broken link → `LinkTargetNotFoundError`; `a↔b` cycle → `ReadError` Link cycle detected. |
| B-25 | verified | `archive-reading` Reading member data: `open`/`read` directory → `ArchiveyUsageError`. Spot-check: `Cannot open member 'etc/': type is 'directory' (not a file)`. |
| B-26 | verified | `error-handling` Usage errors: “using an `ArchiveMember` from another reader” → `ArchiveyUsageError`. Spot-check: foreign member `open` → `ArchiveyUsageError` … does not belong to this reader. **Note:** `archive-reading` Reading member data matrix still says `ValueError` — spec drift vs `error-handling` + code (harvest). Settles-it line pointed at Name lookup; exception type lives under Reading member data / error-handling. |
| B-27 | verified | `archive-reading` Name lookup and member identity: `in` is identity; string → `TypeError` pointing at `get()`. Spot-check: own member `True`; foreign `False`; `"name" in reader` → `TypeError` … use `reader.get(name)`. |
| B-28 | verified | `archive-reading` Context-manager and close lifecycle; `reader-concurrency` draining close. Spot-check: after reader context exit, member stream `read()` → closed-file `ValueError`. Matches ZipFile/TarFile close semantics described in the requirement. |
| B-29 | verified | `[code]` `reading-members.md:152-156` nested `with` ran; `data == b"hello-a"`. |
| B-30 | verified | `access-mode-and-cost` Access-mode enforcement: on `streaming=True`, `members`/`get`/`open`/`read` → `UnsupportedOperationError`. Spot-check: all four raised `UnsupportedOperationError` on NonSeek `tar.gz`. |
| B-31 | verified | Same + summary table: first of `__iter__` / `stream_members` / `extract_all` consumes pass; early `break` still consumes. Spot-check: `__iter__` break then `stream_members` → `UnsupportedOperationError`. |
| B-32 | verified | `access-mode-and-cost` / `archive-reading` Sequential iteration: `scan_members()` drains/finishes after partial pass. Spot-check: after early `break`, `scan_members()` returned full name list. |
| B-33 | verified | `[code]` `reading-members.md:160-164` streaming pipe stand-in (`NonSeek` + `streaming=True` + `stream_members`) ran; 4 members yielded. |
| B-34 | verified | `safe-extraction` One-Shot Extraction API: deliberately no `members=`; subset via `reader.extract_all(members=...)`. Spot-check: `inspect.signature(archivey.extract)` has no `members` parameter. |
| B-35 | verified | `safe-extraction` one-shot matrix: non-seekable → auto streaming extract. Spot-check: `extract(NonSeek(tgz), dest)` ok; `open_archive(NonSeek)` without `streaming=True` → `StreamNotSeekableError`. |
| B-36 | verified | `reader-concurrency` post-materialization worker seam: different members concurrent; same stream needs caller sync. Matches support-matrix / access-and-cost prose. |
| B-37 | verified | `reader-concurrency` Distinct passes remain single-owner (`__iter__` / `stream_members` / `extract_all`). Spot-check overlap already under B-22. |
| B-38 | verified | `access-mode-and-cost` mode × capability: `streaming=True` + `concurrent_members=True` → `ArchiveyUsageError` at open. Spot-check: raised with that combination named. |
| B-39 | verified | `[code]` `access-and-cost.md:130-132` one-liner `open_archive(..., concurrent_members=True)` ran. |
| B-40 | verified | `reader-concurrency` Draining reader close + Lifecycle leases: `close()` idempotent; under `CONCURRENT` can block on in-flight workers; not safe to race blindly. Spot-check: double `close()` after context exit is a no-op. |
| B-41 | verified | Concurrency contract is per-reader (`reader-concurrency` Multiple concurrently-open member streams / ownership tokens). Separate `ArchiveReader` instances do not share that mutable reader state; support-matrix summary matches. (Settles-it citation is the concurrent-members requirement — per-reader scoping is the supporting design.) |
| B-42 | verified | `reader-concurrency` + `access-mode-and-cost`: default single live stream is the cheap one-decode-position path; capabilities opt-in. Matches support-matrix rationale sentence. |
| B-43 | verified | `[code]` `support-matrix.md:117-121` fail-fast demo: second overlapping `open("b.txt")` raised `ConcurrentAccessError` as the comment says. |
| B-44 | verified | `[code]` `support-matrix.md:48-54` fan-out setup block ran (`concurrent_members=True`, `members()` materialize). Snippet itself is materialize-then-comment; no thread body to execute. |
| B-45 | verified | Three migrator contrasts: (1) `tarfile.extractfile` → `None` on directory vs archivey `ArchiveyUsageError` (typed); links follow on both sides here — “non-regular” in migrating is slightly broad. (2) compressed TAR `SOLID` + cost receipt vs silent re-decode (`format-tar`). (3) truncation/corrupt listing → prefix + error via `members_report` (`archive-reading` MemberListReport; `format-tar` Detect truncated TAR). |
| B-46 | verified | `compressed-streams` verify-at-EOF / size-declared mismatch: full `read()` raises with no bytes; chunked delivers prefix then raises. Spot-check on CRC-corrupted ZIP: `read()` → `CorruptionError`; chunked prefix then `CorruptionError`. |
| B-47 | verified | `archive-data-model` uniform member record (`None` when unavailable) + `archive-reading` Opening / uniform reader surface; cost receipts carry format layout. Matches philosophy “differences as data.” |
| B-48 | verified | `[code]` `migrating.md:59-76` before/after ran on temp `backup.tar.gz`: `tf.extractfile("etc/config").read()` == `reader.read("etc/config")` == `b"config-data"`. Dir listing shape differs slightly (`etc` size 0 vs `etc/` size `None`) — not part of the stated equivalence. |

## Notes for coordinator

### Wrong rows
- *(none)*

### Config notes (`cfg`)
- Everyday verification: **`[all]`**.
- RAR data / header-encrypt spot-checks used system `unrar` (present per SESSION).
- B-44 free-threaded CI claim not re-run on 3.13t here; only the published snippet executed.

### Cross-cluster / process
- **B-26 harvest:** `archive-reading` still says foreign `open` → `ValueError`; `error-handling` + implementation use `ArchiveyUsageError`. Prefer aligning `archive-reading` to `error-handling` (or vice versa) — do not silently pick in docs-only without a decision if you treat this as a discrepancy.
- **B-6:** fifth copy on `migrating.md` in addition to the four `scope.md` counted — trim plan should include it.
- Spec **line numbers in Settles-it drift** (same lesson as Worker A); matched by requirement title/text.
- No “Conflicts with X” rows in this cluster; no false-conflict / SPLIT cases.
- Cross-page: B-30/B-31/B-32 overlap Worker covering `access-and-cost` streaming; B-36–B-44 overlap support-matrix threading.

### Counts
- **verified:** 48
- **wrong:** 0
- **unverifiable:** 0
