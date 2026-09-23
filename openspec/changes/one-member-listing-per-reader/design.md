## Context

The base reader has four ways into a backend's `_iter_members()`, and only one of them
stores what it gets back:

| Path | Entry | Stores the list? | Registers members? |
| --- | --- | --- | --- |
| Resolved materialization | `_materialize_members` (`base_reader.py:1140`) | Yes, in `_materialized` | Yes |
| Index-only peek | `_get_members_index_only` (`:1225`), behind `members_report_if_available()` | No: a new walk on every call until materialized | Yes, re-stamping |
| Streaming forward pass | `_begin_forward_pass` → `_ProgressivePassIterator` (`:2358`) | Yes, at EOF, via `_finalize_pass_links` | Yes |
| 7z / solid-RAR data pass | `iter(self._members)` in `sevenzip_reader.py:474`, `rar_reader.py` solid branch | No | **No** |

`extract_all` peeks (`extraction.py:331`) and then materializes (`:343`), so every backend
with an upfront index is walked twice per extraction. ZIP and ISO build new `ArchiveMember`
objects on each walk. Everything downstream that keys on "this member" then has to use the
listing position instead of the object: `_presentation_checked` for the bidi check, and PR
386's `_member_reports` ledger with `reattach_to_member` for typing-time diagnostics. The
listing tracker resets and recounts on every walk for the same reason.

### What earlier decisions say

- **ADR 0007** (mutable `ArchiveMember` filled in place) and the `archive-data-model`
  requirement it backs: members are mutable, the library is their only writer, late fields
  (sizes, link targets, diagnostics) are filled in place on the object the caller already
  holds, and "previously returned members are live objects, not point-in-time snapshots".
  This change applies that rule to one more place, the peek.
- **`archive-reading`, "Sequential in-order iteration"** already requires a completed
  streaming pass to be "finalized in place on already-yielded objects". A streaming reader
  already hands out members before resolving their links and completes them later. The
  random-access peek will do the same.
- **`dev-docs/IDEAS.md`** ("Reuse the index-only pass's members instead of rebuilding them")
  and **`dev-docs/code-map.md`** ("Listing can happen twice…") both say the second set of
  objects was never decided. It falls out of the index-only result not being stored. Both
  flag the listing tracker and the concurrency gate as the places needing care, and so
  does this design.
- **`reader-concurrency`, "Materialization boundary"**: the resolved report and name index
  are built privately and published once as immutable containers, while `ArchiveMember`
  objects keep backend-populated late-bound fields. This design keeps the resolved report's
  containers private until published. The member objects inside may already have been
  handed out by the peek, which the boundary allows because they are not the containers.

Nothing found argues for fresh objects per call.

## Goals / Non-Goals

**Goals**

- `_iter_members()` runs once per reader for a walk that completes, whichever public
  methods are called and in whatever order.
- One `ArchiveMember` object per member per reader, the same across every listing method,
  every pass and every peek.
- Registration (id stamp, presentation checks, listing accounting) runs once per member.
- Delete the per-position dedupe machinery the double walk made necessary.

**Non-Goals**

- Changing when a random-access pass resolves links. The default `_iter_with_data` still
  materializes first, and ZIP symlink targets are still read at materialization.
- Removing 7z's or RAR's private `self._members`. They use it for folder grouping
  (`_folder_members`), solid-prefix sums and `info.member_count`, and may keep building it
  at open.
- Any public API change.

## Decisions

### D1. One walk, owned by the base: an append-only list plus the unfinished iterator

The base holds `_listed: list[ArchiveMember]`, the name index for it, and
`_walk: Iterator[ArchiveMember] | None` (the one `_iter_members()` generator), plus the
walk's outcome once it ends (`done`, or the terminal `CorruptionError` / `TruncatedError`).
A single private method pulls the next member. It stamps `_member_id` / `_archive_id`,
runs the presentation checks, accounts the member with the listing tracker, indexes its
name and appends it.

Everything reads the list by position and pulls when it reaches the end:

| Consumer | Reads |
| --- | --- |
| Index-only peek | Pulls to the end and returns the list. Reads no member data. |
| Resolved materialization | Pulls to the end, resolves links on the listed objects, publishes `_materialized` |
| Streaming forward pass | Holds its own cursor (a position in the list); pulls when the cursor reaches the end of what has been walked; finalizes when the cursor passes the last member of a completed walk (D1b) |
| 7z / solid-RAR data pass | Iterates the listed members by position (D6) |

**D1a. Last-entry-wins `is_current` is stamped once, when the walk ends**, by whichever
consumer ended it, and nowhere else. The walk ends in one of two ways. It completes, or it
stops on terminal damage (D2). With terminal damage, the stamp runs over the recovered
prefix at the point the incomplete report is stored. Both of today's call sites stamp a
damaged prefix, in opposite orders. `_materialize_members`' damage branch
(`base_reader.py:1173-1182`) calls `_finalize_links(..., is_current_first=True)`, which
stamps before link resolution. `_finalize_pass_links` passes `is_current_first=False`,
which stamps after it (`:1137-1138`). Removing `is_current_first` must not remove either
stamp: `ArchiveMember.is_current` defaults to `True`, so an unstamped prefix would make
every shadowed duplicate read as current. Measured on `main`: a streaming pass over a TAR
holding `a.txt` twice and truncated in a later member ends in `TruncatedError`, and its
incomplete report reads `a.txt False`, `a.txt True`. Without the stamp, both would read
`True`, and extracting that prefix would write the shadowed entry, then fail on the later
one with the error described below.

The two orders give the same links. Link resolution never reads `is_current`
(`_lookup_link_target_for_member` and `_resolve_link` do not touch it), so stamping
before or after resolution gives the same result. The only visible difference
is *when* a held member shows its final value:

- A streaming pass nobody peeks into completes the walk at EOF, so members keep today's
  timing.
- A peek into an upfront index completes the walk early, and every member the pass
  yields afterwards already has its final `is_current`. Measured on `c45ce34`: today a
  shadowed `a.txt` reads `is_current=True` for the whole streaming pass while the peek's
  separate objects already read `False`. That is also why **streaming `extract_all()`
  over a ZIP with a duplicate name aborts today**. Its own peek stamps objects the pass
  never uses, the pass writes the shadowed `a.txt`, and the later `a.txt` then fails with
  `ExtractionError: Destination already exists` under the default overwrite policy.
  Random access on the same archive returns `SUPERSEDED, EXTRACTED, EXTRACTED`, which is
  what `safe-extraction` "Skip non-current members by default" requires in either mode.
  After this change the pass yields the peek's stamped objects, and the streaming result
  matches random access. Streaming TAR aborts the same way today, and this change cannot
  fix it because TAR has no index to peek. That is recorded as its own item, not
  claimed here.

The "preserve those orderings" note on `_finalize_links` (`base_reader.py:1095`) goes.
The eager/progressive ordering difference it protects collapses into D1a.

**D1b. A streaming pass finalizes on its cursor, not on the walk.** A peek can drain the
walk while the pass's cursor is still at member 0, so walk exhaustion is no longer the
same event as the pass reaching the end. The pass finalizes (resolves links, publishes
the complete report) when its cursor steps past the last member of a completed walk,
exactly once. A walk drained by a peek, followed by an abandoned pass (`break`, no
`scan_members()`), runs no finalization: it resolves no links and leaves `_materialized`
unpublished. That is `archive-reading`'s "An abandoned pass … SHALL NOT finalize",
unchanged. On ZIP and ISO, where link targets are read only at finalization, the
abandoned pass therefore reads no link data. A 7z pass reads a link member's bytes as its
cursor passes that member (D6b), so an abandoned 7z pass may already have read link data,
and decoded its folder up to that link, for the members it passed.

**Rejected: cache the peek's result and leave the other paths alone.** That is the smallest
diff and fixes ZIP and ISO for `extract_all`. But the streaming pass and the 7z/RAR passes
would still walk on their own, the member-id bug would remain, and the dedupe ledger could
not be deleted, because a streaming peek followed by the pass would still produce two
object sets.

### D2. Failure: discard what nobody saw, keep what somebody did

- **Random access.** No random-access consumer hands out a member before the walk has
  ended. The peek, `members()` and `__iter__` all drain first. So a walk that fails with
  anything other than terminal damage (`ResourceLimitError`, `KeyboardInterrupt`,
  `MemoryError`) leaves a prefix nobody holds. It is discarded along with the tracker's
  counts, and a later call starts a fresh walk, exactly as `_materialize_members` does
  today.
- **Streaming.** The pass has already yielded the prefix, so it cannot be discarded. A
  failed walk poisons the reader as `_ProgressivePassIterator` does today ("reopen the
  archive to retry").
- **Terminal damage** (`CorruptionError` / `TruncatedError`) is a completed walk with an
  error. The prefix and the error are stored once, and last-entry-wins is stamped over
  the prefix at that point (D1a). `members_report()` and the peek return
  the incomplete report; `members()`, `scan_members()` and `get()` raise, as the spec
  already requires.

One small behaviour change follows. Today an upfront-index peek whose walk hits terminal
damage propagates the error. After this change it returns the stored incomplete report,
which is the shape the peek already returns after an incomplete pass. The walks that can
fail this way are rare: ZIP's `infolist()` and 7z's header are parsed at open, so the
per-member typing step is what would have to fail. `extract_all` stays fail-closed
anyway: it uses the peek only for progress totals and the selector (`extraction.py:331`),
then calls `_get_members_registered(enforce_listing_limits=True)` (`:343`), which raises
on the report's error before anything is written. The guarantee is kept by that second
call, not by the peek.

### D3. Link resolution completes the listed objects in place

Resolution runs on `_listed`, and the objects the peek returned are the ones that gain
`link_target`, `link_target_member` and link diagnostics. `_resolve_link_target`'s
per-member `_link_target_resolved` memo already makes resolution idempotent. A resolution
step that fails part-way leaves the members it finished resolved, and a retry resolves the
rest. It does not re-walk.

For a caller, this means a member held from a peek later shows its link fields filled.
That is the ADR 0007 contract ("live objects, not point-in-time snapshots"), already true
of streaming passes. The spec delta states it for the peek.

### D4. Listing limits: account once, enforce per caller

The tracker accounts each member once, when it is pulled. There is no more
`reset()` / re-account per pass, and the "already stamped: re-account" branch in
`_register_member` is deleted.

Enforcement stays per caller, the way `_progressive_enforce_listing_limits` works today:

- A pull made on behalf of an enforcing caller (`members()`, `scan_members()`,
  `members_report()`, the peek, `extract_all`'s prep) raises `ResourceLimitError` as soon
  as a limit is crossed. A ZIP bomb is still refused part-way through the listing, before
  the rest of its members are built.
- A pull made for `stream_members()` / `__iter__` accounts without enforcing, since
  iteration stays the unguarded escape hatch.
- An enforcing caller that finds members already pulled by a non-enforcing one runs
  `assert_within_limits()` on the running totals before returning, as the cached-report
  paths do today.

### D5. Concurrency: the walk has one owner at a time

Pulling from the walk is reader-wide work of the same kind as first-touch materialization.
Under `CONCURRENT` it goes through the same election: one owner walks, others wait on the
condition variable and then read the list. Without `CONCURRENT`, overlap raises
`ArchiveyUsageError` as today. `reader-concurrency` already names
"`members_report_if_available` initialization" as single-owner, and this design makes the
code match. No lock is held while the backend yields or while diagnostics callbacks run,
so the "no reader-state lock held during scan and callbacks" rule stands.

A streaming reader is single-owner by construction. A peek from inside a streaming
`for` loop runs while the pass generator is suspended between yields, so nobody is
mid-pull. For an upfront-index backend, the peek then drains the walk ahead of the pass.
This is safe because walking an upfront index reads no member data (the definition of
`_MEMBER_LIST_UPFRONT`). Draining the walk does not finalize the pass (D1b). For other
backends the peek returns `None` and pulls nothing.

### D6. 7z and solid-RAR data passes iterate the base list

`sevenzip_reader._iter_with_data` and the solid branch of `rar_reader._iter_with_data`
iterate the base list instead of `self._members`. In random access they drain the walk
without resolving links first, which matches today's timing: neither reads link data
before the pass. In streaming they pull through the shared forward pass, so members are
stamped and registered and the pass finalizes at EOF like TAR's.

This fixes the unregistered-member bug in the proposal. It has one consequence: a
streaming pass that finalizes at EOF runs `_resolve_link_target` on its symlinks.

- **RAR** needs nothing. RAR5 carries the target in the header (`file_redir`), and RAR3/4
  targets are read raw from the archive without `unrar`, so EOF resolution never touches
  the solid pipe.
- **7z** stores a symlink's target as the member's data, and `_ensure_link_target` reads
  it through `_open_member`, which on a solid folder decodes from the folder start. How
  much that costs depends on where writers put link data, so it was measured first
  (D6a). The answer is D6b.
- **Memory bound.** Reading link targets once per folder (D6b) keeps what
  `_ensure_link_target` already reads today: the whole member, with no size cap beyond
  the reader's decompression limits. D6b neither widens nor narrows that. A declared-huge symlink is a pre-existing gap: ZIP and
  7z read the whole decompressed member (`stream.read()`) with no cap of their own, while
  RAR reads only stored bytes straight from the archive. It is tracked as its own item,
  and when it is capped, both D6b callers take the same cap.
- A member whose data turns out not to be a link buffer (the reparse-point fallback in
  `_apply_reparse_data`) reverts to a file type at resolution. Today's streaming pass
  already yields such a member as a symlink with a `None` stream, and this change keeps
  that.

#### D6a. Where real 7z archives put symlink data (measured)

A symlink's data is an ordinary content stream, treated like any file's. It sits
**interleaved with file data inside the solid folder**, in the writer's file order, and
it is **compressed with the folder's coder**. It is not stored raw, and it is not placed
at the start of the folder.

Measured with 7-Zip 23.01 (`7z a -snl`) on Linux over a tree of three 27 KB text files,
a 30 KB binary, an x86 executable and four symlinks (targets of 7 to 14 bytes), and on
the Windows junction probe's committed `tests/fixtures/external/junction/junction_7zip_snl.7z`:

| Writer and switches | Where the links land | Coder on the link bytes |
| --- | --- | --- |
| 7-Zip, default (`-mx5`, solid) | Folder 0 at positions 0, 4, 5, 7 of 8, in path order between the text files and the binary. The executable gets its own BCJ folder. | LZMA2, shared with the files |
| 7-Zip `-mx9` | Same positions | LZMA2 (the executable's folder is BCJ2) |
| 7-Zip `-mqs=on` (sort by type) | The three extensionless links first (0, 1, 2), then `zlink.txt` among the `.txt` files at 7 of 8. It is grouped by extension, not by being a link. | LZMA2 |
| 7-Zip `-ms=off` (non-solid) | One folder per member, links included | LZMA2 |
| 7-Zip `-mx0` (store) | One folder per member | Stored |
| 7-Zip on Windows (the junction fixture) | The file symlink's 92-byte reparse buffer at position 1 of 3, between two regular files. Directory links and junctions carry no data at all. | LZMA2 |
| py7zr 1.1.3 (`writeall`) | One folder for everything, links at 0, 5, 6, 8 of 9 | LZMA2 + BCJ over the whole folder, links included |

So the common case, a solid archive, puts link data anywhere in a folder, usually in the
middle and often near the end. Stored or non-solid archives are the cheap exception.

**What that costs today.** `_ensure_link_target` re-decodes the folder from its start up
to the link, once per link. On the default archive above, `members()` decodes
**273 278 bytes** to resolve four link targets totalling 43 bytes. That is exactly
13 + 81 079 + 81 086 + 111 100, each link's end offset in a 111 100-byte folder, so the
folder is decoded two and a half times. The py7zr archive decodes **700 214** bytes
(13 + 223 391 + 223 398 + 253 412), almost three times its 253 412-byte content. The
`-mqs` archive decodes 111 167 because three links happen to sort first. The non-solid
archive decodes 43, only the link bytes. Listing cost therefore grows with
links × folder size, and 7-Zip's defaults are the expensive layout. The bytes were
counted with `io_stats().bytes_decompressed`, and the sums match to the byte.

A streaming pass over the same archive decodes 253 398 bytes today and resolves no
7z link at all. Finalizing it at EOF by re-reading would add the 273 278 again.

#### D6b. Each folder decoded at most once for its links

Since link bytes sit mid-folder, compressed, the unit of work is the folder, not the
link. Each folder is decoded at most once to read its link targets, from its start up to
the end of its **last** link member, and every link member's bytes are kept along the
way. Nothing after the last link is decoded for link targets. There are two callers:

- **Random-access listing** (`members()` / `scan_members()`). The first link resolved in a
  folder triggers the sweep, and the sweep fills every link in that folder. The default
  archive above goes from 273 278 decoded bytes to 111 100, the end of its last link. The
  py7zr one goes from 700 214 to 253 412. In the worst case, every folder ending in a link,
  that is one full decode per folder instead of one per link. This changes how listing
  reads link data, not when, so the Non-Goal on random-access timing stands.
- **The streaming pass.** The pass reads each link member's bytes itself, from its own
  folder decoder, when its cursor reaches that member, and keeps them for EOF
  finalization. The consumer's reads alone are not enough, because link bytes are only
  decoded as a side effect. `stream_members()` yields `None` for a non-file member
  (`archive-reading`, "Non-file stream_members yield None"; `sevenzip_reader._open`
  returns `None` unless `member.is_file`). `SolidBlockReader` also skips lazily: an
  earlier member's bytes are decoded only when a later member of the same folder is opened
  **and read** (`streamtools/solid.py`, class docstring). A folder whose members are all
  skipped is never decoded (`_member_stream_from_solid`). Three cases in D6a would leave a
  link uncaptured without this:
  - a link that is the last member with data in its folder (the default 7-Zip archive's
    fourth link ends at 111 100, the folder's size);
  - a consumer that reads no data, such as `for m, s in reader.stream_members(): pass`, or
    a selector that skips a whole folder;
  - `-ms=off` and `-mx0`, where each link is alone in its folder.

  Reading the link as the pass reaches it moves the pass's decode forward to that link's
  end, through the same `SolidBlockReader`, so earlier unread members are skipped once
  and never re-decoded. Per folder, the pass then decodes from the start to the later of
  two points: where the consumer's reads end and where the last link member ends. This
  happens once, and EOF finalization decodes nothing more. For a consumer that reads
  every stream, the extra cost is only the link bytes after the last file it read. For a
  consumer that reads nothing, the cost is the same as the random-access sweep, which it
  would pay later anyway if it asked for `members()`. The captured bytes are applied at
  EOF, so link fields keep today's streaming timing.

  Rejected: extending the decode only when the pass leaves a folder. That costs the same
  bytes but adds a folder-leave hook to `_iter_with_data`, and a link in the pass's last
  folder would need a second path at EOF. Also rejected: re-sweeping at EOF. That decodes
  the folder a second time, which is the cost D6b exists to remove.

What D6b keeps is bounded by the memory bound above: link members' bytes only,
never file members'. An encrypted folder without a password behaves as today: every link in it gets the
same per-member `EncryptionError` handling and diagnostic that `_ensure_link_target`
gives it now. That holds for the streaming pass too. Reading a link in a folder the
consumer skipped decodes that folder, and so asks for its password. Random-access listing
does the same today. D6b only stops the folder from being decoded once per link.

Rejected: resolving links on demand one at a time and accepting the re-decode. That is
today's behaviour, and the measurements above show it is the expensive path on the most
common writer defaults.

#### D6c. Links the caller's selector excluded (awaiting davitf's ruling)

`archive-reading` promises that `stream_members()` does not open, decompress or request a
password for a member the selector excludes. Selection happens above the backend:
`_iter_stream_members` filters after `_iter_with_data()` yields (`base_reader.py:2160`),
so a backend never learns a member was excluded. EOF finalization resolves every link in
the pass, excluded or not. ZIP already does this today: `_stamp_progressive_member`
records a member before the selector runs, and `_finalize_pass_links` reads every link's
data. Today's 7z pass does not finalize at all, so it reads no link data. After D6 it does,
and on an encrypted folder it would consult the password provider for a link the caller
excluded.

Three contracts were put to davitf:

- **Skip encrypted only** (recommended, and what the deltas say until he rules). Excluded
  links are read, so the complete report matches random access. If an excluded link needs
  a password, the pass tries the known-good and sequence candidates but never consults the
  provider. If none opens it, `link_target` stays unset with `SYMLINK_TARGET_UNAVAILABLE`,
  the diagnostic an unreadable target already gets. The cost of reading excluded links
  stays within D6b's one-decode-per-folder budget.
- **Read every link.** Every link is resolved in both modes, but an excluded link in an
  encrypted folder triggers a password request the caller did not ask for.
- **Selected links only.** The promise holds as written, but the selector must be passed
  down into `_iter_with_data`. Excluded links then stay unresolved in streaming mode, on
  ZIP as well as 7z, which splits `link_target` by mode.

The first option carries a MODIFIED delta for "Bounded-memory sequential streaming via
stream_members". The delta states the link-target exception and amends the two matrix
rows that promised no decode for an excluded member.

### D7. What is deleted

- `_member_reports`, the memo half of `_report_member_diagnostic`, and
  `DiagnosticCollector.reattach_to_member`. What remains of `_report_member_diagnostic` is
  an attaching `emit` with a `diagnostic_logger` override. Inline it into its callers, or
  keep it as that thin wrapper if `archivey.normalization` routing reads better that way.
- `_presentation_checked`; the bidi check runs once because registration does.
- `_register_member`'s "already stamped" branch.
- `_get_members_index_only`, `_pass_scanned` / `_pass_by_name_lists` as separate state from
  the materialized list, and the tracker `reset()` calls in `_begin_forward_pass` /
  `_materialize_members`.
- The `report_key=` arguments at ZIP's and 7z's typing-time emits. The listing position
  itself stays, for D8.
- `_iter_members`' docstring about separate enumerations having to agree. Order stability
  is still worth stating, because a fresh random-access walk after a discarded failure
  (D2) must produce the same ids.

### D8. Typing-time diagnostics carry the listing position as `member_id`

A diagnostic raised while a member is being typed runs inside the backend's walk, before
the base stamps `_member_id`. Today the backends disagree on what goes into the context's
`member_id` for such a diagnostic:

| Backend | Typing-time `member_id` today |
| --- | --- |
| ZIP | The listing position (`index`, `zip_reader.py:824`, `:835`, `:874`) |
| 7z | `member._member_id`, which is `None` at typing time (`sevenzip_reader.py:573`) |
| RAR | `member._member_id`, `None`: members are typed at open (`rar_reader.py:1112`, `:1136`, `:1168`) |
| ISO | Whatever `emit_member_name_normalized` reads off the unstamped member: `None` |

With one walk, the listing position *is* the `member_id` the base will stamp, so every
backend passes its position down (ZIP already does) and fills the context from it. A
caller correlating a diagnostic with `member.member_id` then gets a match on every format
instead of only ZIP. 7z and RAR build their lists at open, so the position is the list
index. ISO gains an `enumerate` over its walk.

## Risks / Trade-offs

- **Peeked members change later.** A caller that peeks, holds a symlink member, then calls
  `members()` sees `link_target` go from `None` to a value on the object it holds. That is
  the documented live-object contract, and today's behaviour on streaming readers. The
  spec delta makes it explicit for the peek.
- **Concurrency regression surface.** The walk now shares the materialization election.
  The existing `tests/test_concurrent_multithread.py` and `test_concurrent_cooperative.py` cover first-touch. A new case covers
  a peek racing `members()` under `CONCURRENT`.
- **Error-path drift.** Three error behaviours (discard, poison, store-incomplete) now live
  on one object instead of three. Each gets a test that fails if it is routed to the
  wrong one.
