# Threat model

This is a design document. It says who archivey defends against and what they control,
where trust stops, which property we promise at each boundary, the mechanism that
enforces it and the tests that pin it, and what we deliberately do not defend. An entry
here changes when the design changes. A bug in a mechanism belongs in
[`known-issues.md`](known-issues.md) until it is fixed; this page does not track defects.

The public half is [`docs/extracting.md`](../docs/extracting.md): §Trust boundaries,
§Known and accepted limits and §What is enforced. This page and that one must agree.
Disclosure and reporter scope are in [`SECURITY.md`](../SECURITY.md).

Older references use register ids (`O1` to `O22`, `C1` to `C4`). The
[index](#6-index-of-old-register-ids) maps each one to its section here.

## 1. Scope and attackers

### Who we defend against

- **Whoever wrote the archive.** Every byte is attacker-controlled: member names, link
  targets, declared sizes, timestamps, comments, header structures, key-derivation costs,
  indexes, and compressed and encrypted streams. Crafted archives are in scope for every
  guarantee, not only well-formed ones. So are sources that are not archives at all,
  since detection runs on whatever the caller hands in.
- **A process writing to a directory source while archivey reads it.** A caller may read
  a tree someone else can write to (an upload staging folder, a shared drop folder).
  That writer can swap files, directories and symlinks between listing and reading.
  Ruled in scope by davi on 2026-09-27 (PR #496's decision card).

### What is trusted

- **The local process**, including the caller's own configuration, filters, password
  providers and limit choices. A caller who raises a limit to `UNLIMITED` gets what they
  asked for.
- **Other local processes, for the extraction destination.** A local attacker racing
  extraction by modifying the destination is out of scope. If that changes,
  `O_NOFOLLOW` / `openat`-style extraction is the direction.
- **The destination at rest.** What was there before extraction is the caller's. What
  extraction itself produced is not trusted: an earlier member is untrusted input to
  the handling of every later one. The destination root is followed if it is a symlink,
  as `tar -C` and `unzip -d` do.
- **Optional libraries and external programs** (`pycdlib`, codec packages, the
  accelerators, `unrar` / `rar` / `unar`) are trusted not to be malicious, but not
  trusted to be robust on hostile input. Their failures should surface as typed errors;
  where that cannot hold, it is an [accepted non-guarantee](#4-accepted-non-guarantees).

## 2. Trust boundaries

| Boundary | What crosses it | What holds there |
| --- | --- | --- |
| Source bytes to parsers | Archive structure | Pure-Python parsers for 7z and RAR headers, stdlib `zipfile` / `tarfile`, `pycdlib` for ISO; every read a header field sizes goes through a bounded source ([resource use](#resource-use-is-bounded)) |
| Parsers to decoders | Compressed streams, declared decoder parameters | `DecoderLimits` checked before allocation; crash-prone native decoders in a child process ([errors](#errors-are-typed-and-honest)) |
| archivey to external programs | Archive path, member selection, password | RARLAB `unrar` / `rar`, or `unar` under `"auto"`; fixed argv; a member name only inside an include-mask switch, or an entry index ([external programs](#external-programs-get-a-fixed-command-line)) |
| archivey to the destination | Names, link targets, modes, data | `check_universal` and the extraction coordinator ([extraction](#extraction-stays-in-the-destination), [names](#names-are-safe-on-the-target-filesystem)) |
| archivey to a terminal or log | Messages carrying archive-derived text | Escaped at construction ([terminal](#attacker-bytes-reaching-a-terminal-are-inert)) |
| Directory source to other processes | The tree between listing and reading | No-follow opens and identity checks ([directory sources](#directory-sources-changed-concurrently)) |
| Caller to archivey | Recursion into nested archives, time budgets | Not defended: the caller bounds them ([non-guarantees](#4-accepted-non-guarantees)) |

## 3. Defended properties

Paths are under `src/archivey/` unless they start with `tests/`.

### Extraction stays in the destination

**Property.** No member writes, links or replaces anything outside the destination, and
no member replaces the destination itself.

**Mechanism.**
- `internal/filters.py` `check_universal` runs on every member under every policy,
  `TRUSTED` included, after the caller's filter, on the name about to be written. Under
  `STANDARD` and `TRUSTED`, `reroot_absolute` first drops a rooted name's root (a
  leading `/` or `\`, or a drive letter with a separator after it), so the member
  lands inside the destination. It rejects `..` components
  (any separator), absolute paths including drive letters and UNC prefixes (every
  absolute name under `STRICT`; at any policy a drive-relative `C:x`, which has no
  root to drop, or an absolute name a filter returned), NUL bytes, names
  `os.fsencode` cannot represent once a lone surrogate is spelled as its UTF-8 bytes
  (`filters.disk_spelling`, which the coordinator applies first), special files
  (devices, FIFOs, sockets), and a non-directory member whose normalized name is
  `"."` or `""` (which would replace the destination root with a file). It resolves
  the parent and checks containment, and checks symlink targets lexically. A
  rejection names the member as listed, not its disk spelling.
- `internal/extraction.py` `ExtractionCoordinator._write_symlink` re-resolves a new
  symlink against the live tree after `os.symlink` and removes it if it escapes, which
  catches a chain staged by earlier members. That is the third layer after the lexical
  target check and the parent resolution. A later member that changes a path the link
  resolved through gets it rechecked
  ([below](#a-later-member-cannot-make-an-extracted-symlink-escape)).
- A hardlink target must name an earlier member, and the member the link gets its bytes
  from must not have been refused (maintainer decision, 2026-10-07). The target is a
  member name, never a path: it resolves to the latest earlier member of that name
  (`internal/naming.py` `resolve_link_target_name`, `..` and a leading `/` kept), so a
  duplicate name cannot redirect a link, and the link is made to the file its source,
  the end of the link chain (`link_target_member`), was written to (`_write_hardlink`,
  `_place_link`). `check_universal` therefore reads nothing of the target string, and a
  filter's change to it does nothing. `ExtractionCoordinator._source_refused` refuses a
  link whose source was refused: this run's result for the source when there is one,
  otherwise the policy's own checks on the source as listed (a selector or filter
  excluded it). Without that, the second pass would write a refused member's bytes, such
  as `../x`'s, under the link's name. A refused link in the middle of a chain refuses
  nothing after it, and a hardlink to a symlink is written as that symlink and gets the
  symlink checks (one to a symlink with no target fails, and is not refused for it). A
  RAR file copy is a `FILE`, so its source lookup keeps the escape test instead
  (`within_root`).
- Overwrites replace a symlink rather than follow it (`_prepare_destination`,
  `_place_link`), and file data is written to a `.archivey-tmp-<random>` sibling
  (`_temp_sibling`) and moved with `os.replace` (`_write_file_atomic`), so an
  interrupted run never leaves a half-written file.
- `_apply_metadata` strips setuid, setgid and sticky except under `TRUSTED`, and applies
  ownership only under `TRUSTED` as root.

**Residual.** A local process racing the destination (out of scope, §1). A hard kill can leave `.archivey-tmp-*`
files, which are safe to delete.

**Tests.** `tests/test_extraction.py`: `test_check_universal_rejects_traversal`,
`test_check_universal_rejects_root_named_file`,
`test_check_universal_rejects_special_file`,
`test_check_universal_rejects_symlink_escape`,
`test_chained_symlink_attack_symlink_payload_rejected`,
`test_chained_symlink_attack_file_payload_rejected`,
`test_hardlink_duplicate_name_extraction_links_first_inode`,
`test_check_universal_names_a_symlink_escape_not_a_hardlink_target`,
`test_replace_symlink_no_write_through`,
`test_hardlink_replaces_a_destination_symlink_without_following_it`,
`test_error_when_dest_is_a_file_never_deletes_it`,
`test_dest_symlink_to_dir_is_followed_into_target`, `test_strict_strips_setuid`.
`tests/test_hardlink_target_rule.py`: `test_a_hardlink_to_a_refused_member_is_refused`,
`test_a_hardlink_to_an_excluded_refused_member_is_refused`,
`test_a_hardlink_to_a_selector_excluded_member_is_materialized`,
`test_a_hardlink_to_a_later_member_still_fails`,
`test_a_filter_that_rewrites_a_hardlink_target_changes_nothing`,
`test_a_filter_that_makes_the_source_unsafe_refuses_its_links`,
`test_a_hardlink_with_a_rooted_or_drive_target_gets_what_its_member_gets`,
`test_a_hardlink_through_a_refused_middle_name_links_to_its_written_source`,
`test_a_hardlink_to_a_refused_symlink_is_written_as_a_symlink`,
`test_a_refused_source_refuses_links_through_a_safe_middle_name`,
`test_check_universal_does_not_read_a_hardlink_target`.
`tests/test_property_safety.py` covers `normalize_member_name`, `check_universal` and
`resolve_link_target_name` over arbitrary input, and the mutation harness asserts the
destination is still a directory after every successful extract.

### A later member cannot make an extracted symlink escape

**Property.** No symlink the archive created is left on disk resolving outside the
destination at any point a later member could observe, in either streaming mode.

A later member can change what an earlier link resolves to. Archive `l -> a/../secret`,
then `a -> .`: when `l` is created, `a` does not exist, so `l` resolves to
`<dest>/secret` and passes. `a -> .` is harmless on its own and passes too. On disk, `l`
then resolves through `a` to `<dest>/../secret`. The same happens when a later member
replaces a directory the resolution went through (`OverwritePolicy.REPLACE` removes it
and puts a symlink there), or removes a symlink it went through, so that its name is read
lexically again. Every file write resolves its real parent first, so nothing was ever
written through such a link; the risk was the link itself, for anything that reads or
copies the tree afterwards. Found by the 2026-09 extraction audit
(`review/archive/2026-09-29-extraction-audit/`, E1).

**Mechanism.** An incremental recheck (the maintainer's direction, 2026-09-28), not an
end-of-run sweep and not a refusal of `a/../x` targets.
- **What is recorded.** When a symlink is created and passes, `internal/link_watch.py`
  walks its target the way `os.path.realpath` does and records every destination path
  the walk `lstat`s: components that do not exist yet, directories it went through, and
  the components of every link it followed on the way (at most 64 follows, more than
  any kernel allows). An absolute target, the link's own or a followed one's, is walked
  from its anchor, so one that re-enters the destination by name is recorded there.
  Only the state of those paths decides where the link resolves. The index folds case
  and Unicode normalization, which can only cause extra rechecks; the record of which
  link sits at which path does not, so `L` and `l` under `TRUSTED` are both watched.
- **What counts as a change.** A symlink created at a path, and a symlink or directory
  removed or replaced there: `REPLACE` and `RENAME`'s directory case in
  `_prepare_destination`, a streaming pass replacing or dropping a superseded copy, a 7z
  anti-item, and a hardlink or file the orphan second pass places over one. A file or
  directory created where nothing was cannot move a resolution and is not reported. A
  change is reported at the path actually written, so `RENAME`'s `name (N)` is covered.
- **The recheck.** After each member, before the next one (and after each write of the
  orphan second pass), every link that depends on a changed path is resolved again
  against the live tree with the same `Path.resolve()` check that runs at creation. A
  link that escapes is unlinked and its result becomes `BLOCKED` with a
  `FilterRejectionError`. Its removal is itself a change, so a link that went through it
  is rechecked in turn. A link that passes gets its dependencies recorded again.
- **Results and progress.** The earlier member's result is revised in place, as a
  `REPLACE` collision revises one to `OVERWRITTEN`. A progress report already sent for
  that member is not sent again; the tallies of the next report (`members_extracted`,
  `members_blocked`) follow the revision. The revision is a policy block, so it never
  stops the run under `OnError.STOP`, and `AbortOn.BLOCKED_MEMBER` ends the run on it.

*Cost.* The walk costs one `lstat` per component, about what the creation check's
`resolve()` already costs, so a `node_modules`-shaped tree stays linear in link count.
Measured on that shape at 2,000 and 4,000 links: 94,013 and 188,013 `lstat` calls for
the whole extraction, against 73,013 and 146,013 with the index disabled, so about 29%
more, and exactly linear. Memory is one index node per distinct path visited plus one
entry per path per link, so it is proportional to the walk work already done. A hostile
archive can still make work quadratic: many links through one path, then many members
changing that path (a streaming pass does that with repeated copies of one name, under
the default policy). Rechecks are therefore bounded by `ExtractionLimits.max_entries`,
the bound on members. Once it is spent, the links still waiting are removed unresolved
and the run stops with `ResourceLimitError`, so nothing unverified stays on disk.

*Options considered and not taken* (2026-09-28): refusing `..` after a normal component
(refuses targets some build tools write, and `REPLACE` breaks its completeness
argument); re-validating every link at the end of the run (the escape stays live on disk
until then); analysing all targets before extracting (not available to a streaming
pass).

**Residual.**
- **Links that were in the destination before the run.** They are followed and their
  paths recorded when an archive link goes through them, but they are never rechecked
  or removed: they are the caller's. An archive member can still make such a link
  escape by creating a path it goes through.
- **Windows.** The walk splits targets on both separators, joins drive- and
  root-relative targets the way `pathlib` does, and follows what `os.readlink` returns,
  but it has not been run there: creating symlinks needs a privilege, and the tests skip
  on Windows. The creation check is unchanged and still runs.
- **A link the filesystem refuses to remove.** Its result says `BLOCKED` and a warning is
  logged, as for a link that escapes when it is created.

**Tests.**
`tests/test_audit_extraction.py::test_symlink_made_escaping_by_a_later_member_is_not_left_on_disk`
and `tests/test_symlink_recheck.py`, including a link reached through a chain, `REPLACE`
in both directions, a streaming pass's later copy, `RENAME`, the orphan second pass, a
7z anti-item, the order of rechecks inside one member, legitimate `a/../x` targets, the
bound, and `::test_symlink_heavy_extraction_stays_linear_in_link_count` (counts `lstat`
calls).

### Names are safe on the target filesystem

**Property.** Under `STRICT` and `STANDARD`, a member name cannot capture a device, write
an NTFS alternate data stream, silently merge with another member on a case-insensitive
or normalizing filesystem, or fail unpredictably because the filesystem refuses its
bytes. The outcome is the same on every platform. ADR
[0013](decisions/0013-cross-platform-name-safety-policies.md) is the design.

**Mechanism.** `internal/filters.py` `apply_name_policy`:
- Reserved device names (`_RESERVED_NAMES`: `CON`, `NUL`, `COM1`, ..., the ports
  spelled with a superscript digit such as `COM¹`, `CONIN$`, `CONOUT$`) and `:` are
  rejected on every platform. Both are unsafe, not merely non-portable.
- `STRICT` strips a trailing dot or space (`_strip_trailing_dot_space`), because Win32
  trims it silently and the reported path would differ from the real one. The rewrite is
  recorded as `ExtractionResult.presented_name`, the only record that survives a caller
  filter rename. An all-dots segment has no portable spelling and is rejected.
  `STANDARD` and `TRUSTED` keep the name.
- Two classes of character are percent-escaped (`_sanitize_portable_name`: `%XX`,
  literal `%` as `%25`), a deterministic and reversible spelling: non-UTF-8 bytes, and
  the characters Win32 refuses in a name, `<>"|?*` and the controls 0x01 to 0x1F.
- A `\` in a name, which TAR keeps as a literal character, is written as `/`, as
  Windows writes it, and a symlink or hard link target gets the same rewrite. A hard
  link still resolves to the member the reader matched to its stored target.
- The coordinator (`ExtractionCoordinator._transform`) runs `check_universal` again on
  a member `apply_name_policy` rewrote. A rewrite can change which directories a path
  passes through (`foo\x` and `foo. /x` both become `foo/x`), and the first check saw
  only the stored spelling, so a `foo` symlink in the destination that leaves it was
  not checked on the path actually written.
- `collision_key` casefolds and NFC-normalizes. The coordinator
  (`_register_collision_key`, `_resolve_collision`, `_derive_free_name`) treats a
  collision as an event: `OverwritePolicy` applies, `RENAME` writes `photo (1).jpg`, and
  a `REPLACE` revises the clobbered member's result to `OVERWRITTEN` (`_mark_overwritten`)
  so the merge is visible. `ExtractionReport.results` is the only record.
  `abort_on={AbortOn.NAME_COLLISION}` makes any collision fatal;
  `abort_on={AbortOn.NAME_SANITIZED}` refuses any rewritten name.
- A name the filter accepted but the filesystem refuses at write (`EILSEQ` on APFS,
  `ENAMETOOLONG`, Windows `winerror` 123 and 206) is translated to a typed
  `ExtractionError` naming the member (`_typed_os_error` in `internal/extraction.py`).
  So is Windows `winerror` 1314, a symlink the process lacks the privilege to create.
- On Windows the coordinator creates a symlink's target with `\` for `/`
  (`_link_target_on_disk`), so a relative target resolves as on POSIX. It clears the
  read-only attribute on a regular file or empty directory this run wrote before a later
  member replaces it or an anti-item removes it (`_readonly_cleared`), and leaves a
  read-only entry the caller already had alone. A hard link past the filesystem's
  link-count limit is written as a copy (`_link_refused_here`), counted toward
  `max_extracted_bytes` and the archive-wide `max_ratio` (see
  [Extraction bombs](#extraction-bombs)).
- Bidi overrides and isolates (U+202A to U+202E, U+2066 to U+2069) in a name or link
  target are rejected under `STRICT` and `STANDARD` (`_reject_bidi_override`); directional
  marks are allowed. `TRUSTED` lifts this rule because nothing about the write is unsafe,
  only the name read back later (ADR
  [0017](decisions/0017-bidi-override-rejection-is-policy-keyed.md)). Listing always
  presents the stored name with `MEMBER_NAME_BIDI_CONTROL`.

`TRUSTED` keys collisions on the exact path and writes faithful bytes; the local OS
decides.

**Residual.** Directories are not in the collision map, because they merge structurally,
so a file `Foo` against a directory `foo/` is OS-dependent (ADR 0013; public in
extracting.md §What is enforced). There is no public un-escape helper for the
percent spelling; it can be added without breaking anything.

**Tests.** `tests/test_extraction.py`: the `test_o2_*` collision tests,
`test_o3_reserved_name_rejected`, `test_o3_reserved_name_written_under_trusted`,
`test_o4_colon_rejected_strict_and_standard`,
`test_o3_trailing_dot_space_stripped_strict_kept_standard`,
`test_o7_percent_escaped_when_sanitizing`,
`test_o7_sanitized_name_collides_with_literal_percent_name`,
`test_unrepresentable_name_oserror_is_translated`, and the `test_bidi_*` tests.
`tests/test_cross_os_extraction.py` covers the separator rewrite, the Win32 character
escapes, the further device names, the typed Windows errors, symlink targets on Windows,
read-only entries and the link-count copy; the re-check after a rewrite is
`::test_rewritten_name_is_checked_where_it_is_written`.

### Resource use is bounded

Each budget below is a byte, count or rounds budget. None of them bounds time; that is
[accepted](#no-cpu-or-wall-clock-bound).

#### Listing

**Property.** Listing a hostile archive costs at most the listing budget, not what the
archive declares.

**Mechanism.**
- `ListingLimits` (`max_members` 1,048,576, `max_metadata_bytes` 64 MiB) is enforced by
  `internal/listing_limits.py` `ListingLimitTracker` as members are registered into a
  materialized list (`members()`, `scan_members()`, extract preparation). Crossing a cap
  raises `ResourceLimitError`. `None` (`ListingLimits.UNLIMITED`) disables it.
  `stream_members()` and `streaming=True` are unguarded, as the O(1) escape hatch,
  except on 7z, RAR and ISO, which check the caps while parsing. Unguarded bounds memory,
  not work: a forward-only TAR walk reads through every member it skips, for the bytes
  present rather than the size a header declares (a member declaring more than the
  archive holds raises `TruncatedError` at the first short read).
- 7z checks `max_members` while parsing, at `open_archive`
  (`internal/backends/sevenzip_parser.py`, the `max_members` checks on folders, unpack
  streams, their sum and `num_files`). Member-scaled counts are also checked against the
  header buffer size (`CorruptionError`), because some counts (`NumUnpackStreams` with
  no sizes or CRCs) read no bytes per entry, and a count of 2^40 would otherwise allocate
  until `MemoryError`. Pack streams keep the header-size bound only: a BCJ2 folder has
  four, and `max_members` would refuse a legitimate non-solid BCJ2 archive.
  Per-folder coder graphs are capped at 7-Zip's own limit of 64 coders and 64 in-streams
  (`k_Scan_NumCoders_MAX`, `k_Scan_NumCodersStreams_in_Folder_MAX` in 7-Zip 26.03's
  `CPP/7zip/Archive/7z/7zIn.cpp`), refused as `UnsupportedFeatureError`; out-streams
  keep the same 64. A larger cap let a small header drive the planner and the nested
  decode streams into a raw `RecursionError`.
- 7z decodes one encoded-header layer and raises `CorruptionError` if the result is
  another encoded header (`internal/backends/sevenzip_pipeline.py`
  `parse_decoded_header`); a COPY header that decodes to itself would otherwise loop.
  The running total of encoded-header folder unpack sizes is capped at
  `MAX_NEXT_HEADER_SIZE` (64 MiB) before any buffer is allocated.
- RAR checks `max_members` while parsing the member table at `open_archive`. It weighs
  the summed declared sizes of compressed RAR 1.5/2.x comments against
  `max_metadata_bytes` before decoding any, because the decode is the cost (one `unrar`
  spawn each). RAR5 quick-open records that are not FILE never become members; their
  bound is `_RAR5_QO_PAYLOAD_MAX` (16 MiB, `internal/backends/rar_parser.py`) and their
  parse is linear.
- ISO checks both caps while `pycdlib` parses, inside `open_fp` at `open_archive`.
  `pycdlib` builds every directory tree of the image there, hundreds of bytes of Python
  objects per record, so the post-open tracker came too late: 3000 records under
  `max_members=10` peaked at about 18 times the image. archivey does not parse ISO
  itself; `internal/backends/iso_reader.py` hooks `pycdlib`'s `DirectoryRecord.parse`
  and the existing `RockRidge.parse` filter (both act only inside `IsoReader`'s own
  `open_fp`, through a `ContextVar`) and counts, per volume descriptor tree, every
  record but `.` and `..` against `max_members`, and the bytes of each directory record
  plus each Rock Ridge continuation area against `max_metadata_bytes`. A continuation
  area is weighed every time it is parsed: `pycdlib` accepts any number of records whose
  `CE` names one area and parses it again for each, which made a 174 KB image peak at
  about 10.7 MB before. A third hook, on `PyCdlib._parse_path_table`, weighs each path
  table's declared size (little- and big-endian, both parsed) with its tree before
  `pycdlib` reads it, and refuses a table that runs past the image as
  `CorruptionError` whatever the limits: `pycdlib` parses a table into one object per
  8-byte record, about 29 times its size, and a 16 MiB table peaked at 471 MiB before.
  Bytes alone still let a table of the whole budget through (about 1.8 GB at the
  default), so a hook on `PathTableRecord.parse` also counts each entry against
  `max_members` and refuses a table of more than `max_members + 1` entries, capping one
  table near 240 MB at the default (maintainer ruling, 2026-10-06). Real images never
  notice: every entry is a directory, and every directory but the root is a member.
- TAR has no member table, so the caps bind the header walk: `tar_reader.py` pulls
  headers in batches that stop one header past what either cap has left, PAX keywords
  and values included. `tarfile` reads a PAX extended or global header, or a GNU long
  name or link name, whole in one call, so the walk refuses such a header from its
  declared size before that read: in random access when it declares more than is left
  of `max_metadata_bytes`, and in any mode, streaming included, when it declares more
  than the whole cap. An over-limit tar then costs about the cap plus one ordinary
  header. A sparse map is weighed only once parsed (24 bytes per entry), so an old GNU
  sparse member's chain of extension blocks, or a PAX sparse 1.0 map, is held whole for
  the one member that crosses the cap.
- A symlink target stored as member data (ZIP, 7z, RAR3/4) is read with a cap of
  `MAX_LINK_TARGET_BYTES` (4096, Linux `PATH_MAX`; `internal/base_reader.py`). A member
  declaring more is not opened; a read with no declared size stops at 4097 bytes. An
  over-long target is left unset, never truncated, with `SYMLINK_TARGET_UNAVAILABLE`
  (`reason="target_too_long"`), per the maintainer's ruling that such a target is
  corrupt or malicious. Without the cap, a 400 KB ZIP whose target was 400 MiB of zeros
  peaked at 2,400 MiB inside `members()`. A target resolved after registration is added
  to the tracker as it arrives, so `max_metadata_bytes` sees it. A Windows reparse
  buffer is read only as far as its own header declares. `read_link_targets=False` stops
  listing from reading these targets at all.

**Residual.** Format-local parser ceilings allocate up to their limit during
`open_archive` (7z: `max_members` or header size, whichever is tighter). The header-size
bound is measured on the *decoded* header, and header compression shrinks a uniform member
table to almost nothing: a 315-byte 7z declaring 1 000 000 directory entries costs about
858 MB and 16 s at open under the default `max_members` (about 860 bytes and 16 µs per
member, the fixed cost of a member object, so names add little and `max_metadata_bytes`
does not see it). The cost is linear in the count the caller allowed, so lowering
`max_members` is the mitigation for a caller opening untrusted 7z, RAR or ISO.

The 1 048 576 default stays (davitf, 2026-10-01), for these reasons:

- The cost per member is the same an honest archive of that many members pays, so a
  crafted header buys no amplification per member. Header compression makes the input
  small, not each member dearer.
- `max_members` bounds it linearly and is the caller's to set: 262 144 caps it near
  250 MB.
- A lower default refuses real large archives for every caller, to protect only those
  opening untrusted input with default limits.
- A ratio check (declared entries per compressed header byte) needs a tuned threshold,
  and an honest header of similar names compresses well too, so it would refuse real
  archives.

Revisit this if a stricter safety-first configuration mode lands (it could carry a
lower default), if member records become lazy (removing most of the fixed cost), or if
callers are found to open untrusted 7z with default limits. ZIP builds the
whole central directory at open through stdlib `zipfile` (ADR
[0006](decisions/0006-stdlib-zipfile.md)), so its memory at open is linear in the
central directory and `max_members` binds at `members()`. `max_metadata_bytes` counts
retained metadata, not a transient decode buffer discarded before any member exists.
ISO's counts are a superset of the listing's: the extra records of a multi-extent file
and the Rock Ridge `rr_moved` scaffolding count as members, and the bytes are records as
stored, System Use areas included, not only the text kept. An image right at a cap can
therefore be refused at open. Below the caps `pycdlib` still builds the whole tree, so
memory at open stays linear in the records the budget allows: about 0.8 KB per plain
record measured, so roughly 1 GiB at the default `max_members`. The UDF descriptors
`pycdlib` also walks are not counted; archivey lists no UDF namespace.

**Tests.** `tests/test_listing_limits.py` (including
`test_tar_listing_stops_reading_headers_at_max_members`,
`test_stream_members_unguarded_when_members_would_fail`);
`tests/test_sevenzip_reader.py::test_num_unpack_streams_count_is_bounded`,
`::test_num_unpack_streams_sum_across_folders_is_bounded`,
`::test_member_scaled_counts_respect_max_members`,
`::test_encoded_header_self_copy_is_typed_corruption`,
`::test_encoded_header_huge_unpack_size_is_typed_corruption`;
`tests/test_rar_reader.py::test_rar_parser_max_members_at_parse`,
`::test_rar3_compressed_comments_over_metadata_budget_refused_before_decode`,
`::test_rar5_qo_non_file_records_parse_in_linear_time`; `tests/test_link_target_cap.py`;
`tests/test_iso.py::test_listing_limits_count_records_as_pycdlib_parses_them`,
`::test_listing_limits_count_directory_record_bytes_at_open`;
`tests/test_audit2_iso_dir_detect.py::test_iso_listing_limits_bound_the_memory_spent_at_open`,
`::test_iso_shared_continuation_area_does_not_multiply_memory_at_open`,
`::test_iso_max_metadata_bytes_counts_a_shared_continuation_each_time`;
`tests/test_iso_metadata_bounds.py`.

#### Allocations sized by a header field

**Property.** A size field in a header never sizes an allocation larger than the bytes
the source really has.

**Mechanism.** stdlib `tarfile` reads a PAX extended header or GNU long name with one
`read(size)`, and pycdlib reads a directory extent with one `read(data_length)`; both
sizes come straight from the archive. Measured without a bound: a 10 KB tar asked for
6 GiB, and a 51 KB ISO asked for 4 GiB, both dying on a bare `MemoryError`. So both
libraries read through archivey's source (`internal/source.py` `ArchiveSource`) or
decompressor (`tar_reader.py` `_BoundedTarFileobj`), and both apply
`streams/streamtools/binaryio.py` `read_within_reach`: where the remaining length is a
fact (a path's `stat`, a `BytesIO` buffer, a regular file's `fstat`) the read is clamped
to it; otherwise it is served in bounded steps, so the peak tracks the bytes that exist.
An fsspec `size` attribute is a hint, not a fact, and is stepped. A short read then
fails in the library and is translated to `CorruptionError`. The ISO reader passes every
source to `open_fp` as an `ArchiveSource`, a path included, so there is always something
of archivey's under pycdlib; `open_archive` closes the source if the reader never
finishes constructing. Two sizes are checked before the read rather than left to the
source, because the source bounds them only by the image: a Rock Ridge `CE` entry's area
must end inside its logical block, as pycdlib itself requires after its read and the
Linux kernel requires, so a `CE` declaring 512 MiB in a sparse 600 MiB image is refused
without the read (545 MiB peak before), and so is each further link of a `CE` chain,
which pycdlib follows from 1.21 (a 256 MiB second link peaked at 256 MiB under default
limits before); and a path table must end inside the image ([Listing](#listing) weighs
it against `max_metadata_bytes` too). This bounds one read; how many records and
continuation areas `pycdlib` builds from those reads is the listing budget's
([Listing](#listing)).

A flat metadata cap would be wrong here: member data goes through the same wrapper, so a
40 MiB member arrives as one 40 MiB request.

**Tests.** `tests/test_tar.py::test_extended_header_size_does_not_drive_the_allocation`;
`tests/test_iso.py::test_directory_data_length_does_not_drive_the_allocation`,
`::test_a_path_source_refuses_the_same_image`,
`::test_a_refused_path_source_does_not_hold_its_handle`;
`tests/test_iso_metadata_bounds.py`:
`::test_a_continuation_area_past_its_block_is_refused_before_pycdlib_reads_it`,
`::test_a_chained_continuation_area_past_its_block_is_refused_before_the_read`,
`::test_a_path_table_past_the_image_is_refused_before_pycdlib_parses_it`.

#### Decoder memory

**Property.** A codec never allocates a working set larger than
`DecoderLimits.max_decoder_memory` (2 GiB) because the archive declared one.

**Mechanism.** `internal/config.py` `check_decoder_memory` runs before the decoder is
built, on `open()` and `read()` as well as extraction. A 7z folder can hold several
decoders live at once: a BCJ2 folder runs all its branch decoders (three as 7-Zip writes
it, four if a crafted folder codes `rc`), and the stages of a linear chain are stacked
streams (`LZMA2 → Copy → LZMA2` keeps two dictionaries live). So for every folder with
more than one such decoder, `internal/backends/sevenzip_pipeline.py`
`open_folder_pipeline` checks their summed LZMA dictionaries, PPMd sizes and zstd windows
against the same cap before building any. A zstd window is in the frame header, not the
coder properties, so only the first frame's counts, and only when the zstd coder reads a
pack stream directly; each later frame is held to the cap on its own. Bytes decoded inside a branch
never reach the folder stream `ExtractionLimits` counts, so the end-of-output check reads
at most one byte from each branch.

RAR data is decoded by `unrar` or `unar` in another process, and the dictionary its
headers declare is checked the same way, before that process starts
(`internal/backends/rar_reader.py` `RarReader._check_dictionary_memory`). The count is
what the program that will run allocates, measured per program (rar.md §7): `unar`
touches the whole declared dictionary; `unrar` touches at most the unpacked bytes the
read decodes, including the earlier members a shared name mask makes it decode.

**Residual.** Detection decodes an LZMA or compressed-tar sample uncapped, so under a
memory cap an oversized declaration can surface as `MemoryError` from `open_archive`
(public in extracting.md §Limits). The RAR counts rest on measurements of `unrar` 7.00
and `unar` 1.10.1; another version that allocates differently is not measured.

**Tests.**
`tests/test_sevenzip_bcj2.py::test_bcj2_folder_dictionaries_count_together_against_the_decoder_cap`;
for RAR, the tests after "the RAR dictionary counts against
DecoderLimits.max_decoder_memory" in `tests/test_audit_rar_iso_dir.py`.

#### Key derivation

**Property.** The total password-to-key work one open archive can demand is bounded,
however many salts or candidates it involves.

**Mechanism.** 7z (`NumCyclesPower`) and RAR5 (`kdf_count`) store their derivation cost
in the archive, each capped at 2^24 per derivation. That bounds one derivation, not the
total: a crafted RAR can use a fresh salt per member at the maximum cost and a PswCheck
no password matches, which costs 4.4 s per member per candidate (0.013 s at the usual
2^15). So `DecoderLimits.max_key_derivation_rounds` (default `2**27`, maintainer,
2026-09-23) bounds summed declared rounds per open archive. `internal/config.py`
`KeyDerivationBudget.spend` charges a cache miss before the derivation runs, since the
derivation runs in `hashlib` and cannot be interrupted. The caches
(`internal/backends/sevenzip_aes.py` `SevenZipKeyCache`, `rar_parser.py` `RarKdfCache`)
make an honest archive cost one or two derivations. RAR3 is charged its fixed `2**18`;
ZIP AES's fixed 1000 rounds are not counted. The budget is in rounds, not derivations,
because a derivation count would either refuse honest per-member salting or admit
hostile archives depending on declared cost. A spent budget raises `ResourceLimitError`,
which the RAR header walks let through so candidate iteration stops rather than reading
it as a wrong password. A stricter preset (`2**24`) is recorded in
[`IDEAS.md`](IDEAS.md).

**Tests.** `tests/test_key_derivation_budget.py`.

#### 7z password confirmation

**Property.** Confirming a password on an encrypted 7z folder holds O(chunk) memory and,
for most folders, decodes a bounded prefix per candidate.

**Mechanism.** 7z AES has no check value, so a candidate is judged by decoding and
checking a CRC. `internal/password_confirm.py` plans the confirmation as a ladder: it
stops at the earliest CRC covering at least 4 bytes, so a solid folder's first member
decides. A chain holding a codec in `REJECTING_CODECS` (LZMA, LZMA2, BZip2, Deflate,
Deflate64, Zstandard, LZ4) stops its bounded probe at `PASSWORD_CONFIRM_PREFIX_BYTES`
(64 KiB of output), because that decoder settles most wrong keys inside the prefix, and
its compressed input is capped at `PASSWORD_CONFIRM_MAX_INPUT_BYTES` (1 MiB). A candidate
that survives the probe is not accepted outright: every remaining static candidate is
probed too (one key derivation each, bounded in total by
`DecoderLimits.max_key_derivation_rounds`, see [Key derivation](#key-derivation)), and
when more than one survives, each survivor in turn walks to the folder's end CRC
whatever the codec, with no input cap, until one matches. The decode streams in 64 KiB
chunks with a running CRC; holding the decoded folder instead cost about three times the
folder size (630 MB peak for a 200 MiB folder). ZIP and RAR3/4 run the same rule
(`password_confirm.attempt_with_confirm`): a ZIP member's probe walks to its CRC or
WinZip AES HMAC for survivors; a RAR3/4 member's probe is up to 64 KiB of `unrar p`
output, its full check the whole member through `unrar p` against the CRC, and a stored
RAR 2.9+ member's only check is a native AES decrypt of the whole member with a running
CRC.

**Residual.** Two shapes still walk a whole unit per candidate: a 7z Copy, PPMd, Brotli
or filter-only chain whose only CRC is at the folder end, and any ZIP, 7z or RAR3/4 unit
where several candidates survive the bounded probe (for RAR3/4 about one wrong password
in three does; a stored RAR4 member is walked for every candidate). That is time, not
memory, and is an [open gap](#7z-password-confirmation-on-a-late-crc).

**Tests.** `tests/test_password_confirm.py`, `tests/test_sevenzip_password_confirm.py`.

#### Spooling a stream source

**Property.** Copying a stream to a temp file for an external program is bounded.

**Mechanism.** RAR member data goes through a program that reads only files, so a RAR
opened from a stream is spooled (`internal/spool.py`), capped by `SpoolLimits.max_bytes`
(1 GiB) and checked before anything is written. A path source is read in place, except
the prefixed and unlinkable cases the `SpoolLimits` docstring lists, which are capped the
same way. The same cap holds the file a solid RAR pass keeps file-copy sources in
(`rar_copy_sources.py`); that keep is charged from its first written byte to the end of
the pass, and declined rather than refused when the cap has no room.

#### Extraction bombs

**Property.** `extract` stops a decompression bomb before it fills the disk.

**Mechanism.** `internal/extraction.py` `BombTracker` enforces `ExtractionLimits`: total
bytes (2 GiB), per-member ratio and archive-wide ratio (1000, active after 5 MiB), a
live ratio for sources of unknown size, and an entry cap (1,048,576). The global guards
raise `_AlwaysStopResourceLimitError`, so they halt even under `OnError.CONTINUE`.

**Residual.** The tracker is per archive and not nesting-aware
([accepted](#nested-archive-amplification)). `read()` and `open()` have no output bound
([accepted](#reads-have-no-output-bound)). A hard link the filesystem refuses at its
link-count limit is written as a copy, so a declared link count drives real writes: one
copy of the source per limit's worth of links (1024 names on NTFS, 65000 on ext4).
`BombTracker.count_copy` counts those copies toward `max_extracted_bytes` and the
archive-wide `max_ratio` (maintainer ruling, 2026-10-07), so a small archive declaring
many links to one member trips the ratio. A cross-device copy counts toward
`max_extracted_bytes` only: it depends on the destination, not the archive.

**Tests.** `tests/test_extraction.py::test_per_member_ratio`,
`::test_archive_wide_ratio`, `::test_archive_wide_ratio_live_denominator`,
`::test_zip_bomb_per_member_ratio`, `::test_streaming_targz_bomb_caught_by_live_ratio`,
`::test_streaming_live_ratio_halts_under_continue`,
`::test_link_limit_copies_count_toward_the_archive_wide_ratio`;
`tests/test_cross_os_extraction.py::test_link_limit_copies_trip_the_archive_wide_ratio`.

#### RAR reads by glob-named member

**Property.** Reading one RAR member never quietly decodes other members first.

**Mechanism.** `unrar` selects members by mask, and a member whose name is a glob that
matches earlier entries would make `unrar -n` decompress every earlier match first. The
read is refused by default when that skip is nonzero (`internal/backends/rar_reader.py`,
`rar_allow_glob_member_concatenation` as the escape hatch). The refusal is narrower
than it looks: on a solid archive an out-of-order `open()` decodes everything ahead of
the member anyway, with no glob involved, so the glob adds only a bounded transfer cost
there. Whether the refusal earns its keep is parked with the general unbounded-read
question ([`formats/rar.md`](formats/rar.md) §5 to §7).

**Residual.** A mask with no glob that still selects earlier members (a duplicate name,
or two names `unrar` reads the same way) is not refused, so `unrar` decodes those
members first. Their dictionaries count against `DecoderLimits.max_decoder_memory`
([Decoder memory](#decoder-memory)); the bytes they decode count against no limit.

**Tests.** `tests/test_rar_reader.py::test_glob_member_with_earlier_matches_is_refused`,
`::test_solid_glob_refusal_does_not_claim_an_avoidable_decode`,
`tests/test_audit_rar_iso_dir.py::test_unrar_counts_an_earlier_member_its_shared_mask_decodes`.

### Integrity verdicts come from reads

**Property.** A member read from its start to its end with no seek verifies every
checksum or tag the archive stores, and raises if one does not match. A verdict is never
deferred to `close()` (ADR
[0014](decisions/0014-integrity-verdicts-from-reads-not-close.md)). A wrong password is
never reported as success. Public:
[errors-and-diagnostics.md §The integrity guarantee](../docs/errors-and-diagnostics.md#the-integrity-guarantee).

**Mechanism.**
- `internal/streams/verify.py` `MemberVerifier`, fused into `ArchiveStream`, hashes
  sequential reads and checks at the declared size or decoder end; a sized mismatch
  withholds the final chunk.
- xz and lzip forward reads never trust the file's index: liblzma checks every block
  against the stream index, and the lzip reader verifies every trailer field (CRC-32,
  `data_size`, `member_size`). Seek points a forward read records have already been
  checked. A cold seek is [accepted](#a-seek-trusts-the-files-own-index) to trust the
  index.
- 7z header encryption has no check value, so a wrong key is caught by the encoded-header
  folder CRC when the writer stored one (7-Zip does; py7zr does not), then by the parse
  failing. About 1 in 256 wrong keys decode to a leading `END` (or `HEADER`+`END`) that
  parses as an empty archive (measured about 0.3% of py7zr salts). Legitimate writers
  never encrypt an empty header, so `SevenZipReader._decode_encoded_header_block`
  rejects a decoded header with zero file records as `EncryptionError`.
- A password only a weak check accepted, or none tested (RAR3/4 encrypted data has no
  check), is confirmed by the member's own CRC at EOF. Closing such a stream early emits
  `ENCRYPTED_MEMBER_UNVERIFIED`.

**Residual.** Wrong-key 7z header garbage that parses into a non-empty plausible header
survives in principle. Rejecting trailing bytes in the decoded header, or py7zr writing
the encoded-header CRC, would narrow it further. Bytes returned before an error are of
unknown quality.

**Tests.**
`tests/test_codecs.py::test_verify_mismatch_raises_at_eof_without_losing_final_chunk`,
`::test_verify_sized_mismatch_withholds_on_reaching_read`;
`tests/test_seekable_streams.py::test_lzip_trailer_member_size_mismatch_raises_on_forward_read`;
`tests/test_sevenzip_reader.py::test_header_encrypted_empty_decoded_header_rejected`;
`tests/test_encrypted_member_unverified.py`.

### Errors are typed and honest

**Property.** A hostile archive produces an `ArchiveyError` subclass or success, never a
raw library exception, and never a silent wrong answer. Genuine I/O errors propagate
unchanged, and no handler swallows or reclassifies an unknown exception.

**Mechanism.** Each backend translates its library's exceptions. For ISO,
`IsoReader._translate_exception` maps `_PYCDLIB_ERRORS`: pycdlib's exception base plus
the bare `IndexError`, `struct.error`, `UnicodeDecodeError`, `AttributeError`,
`KeyError` and `ValueError` fuzzing found it raising, but never `OSError`. Advisories
that are not errors are `Diagnostic` values with stable codes and a per-code policy
(`IGNORE` / `COLLECT` / `RAISE`), attached to the surface they concern, with logging as
the zero-configuration projection. Native
decoders known to crash on crafted input run in a child process: the rapidgzip
accelerator for gzip, zlib and raw DEFLATE (`internal/streams/rapidgzip_child.py`), and
PPMd members over `DecoderLimits.max_ppmd_in_process_input` (16 MiB,
`internal/streams/ppmd_child.py`); a fault signal there becomes `CorruptionError` and
costs only the member.

**Residual.** `MemoryError` passes through, and an in-process native decoder can still
abort the process ([accepted](#a-native-decoder-crash-or-memoryerror)).

**Tests.** `tests/test_error_translation.py`, `tests/test_ppmd_crash_isolation.py`,
`tests/test_accelerator_truncation_abort.py`, and the fuzz layers below.

### Parsers survive hostile bytes

**Property.** Container and header parsing (the part of the defended surface archivey
writes itself) is exercised against mutated and coverage-guided input.

**Mechanism.** Three fuzz layers; the first two run with accelerators off:
1. `tests/test_mutation_fuzz.py` mutates every corpus archive (truncations, bit flips,
   zeroed blocks, garbage prefixes and suffixes) and drives open, list, read, extract
   and detection, asserting a typed error or success, no hang, and a destination that
   is still a directory. `ARCHIVEY_FUZZ_MUTATIONS` deepens it; `ARCHIVEY_FUZZ=1` enables
   `tests/fuzz_sevenzip_parser.py` for local runs.
2. `tests/test_property_safety.py` (Hypothesis) over the pure safety logic.
3. `tests/atheris_fuzz/` (Atheris, coverage-guided) over 7z and RAR header parse with CRC
   fix-up, 7z/RAR open and list, `detect_format` with a cost-within-budget assertion,
   ZIP open and bounded member read, TAR/ISO open and list, and the standalone codecs.
   Four accelerator targets (`gzip_accel`, `zlib_accel`, `deflate_accel`,
   `bzip2_accel`) decode each input with the accelerator on and off and fail when the
   accelerated bytes differ, before or after a seek, or when it ends cleanly where the
   decode with it off raised. zlib and raw DEFLATE inputs carry a declared size, as a
   ZIP member or 7z coder does, since `AUTO` engages rapidgzip on them only with one.
   libFuzzer's own `-timeout` and `-rss_limit_mb` bound those targets: unlike a Python
   alarm, they fire while the main thread is inside native code. A
   short partition runs on every pull request; the full one on a change-guarded nightly
   and on `workflow_dispatch` (`.github/workflows/atheris-fuzz.yml`,
   `openspec/specs/testing-contract/spec.md`). `atheris` is in the `fuzz` dependency
   group only.

pycdlib loops forever when corrupt directory records form a back-edge, in any namespace
`open_fp` walks. `internal/backends/iso_reader.py`
`_install_pycdlib_directory_cycle_guard` installs a queue, confined to archivey's own
`open_fp` call, that drops a directory extent already scheduled; valid trees never
revisit one.

Disclosure goes through GitHub private vulnerability reporting ([`SECURITY.md`](../SECURITY.md)).
OSS-Fuzz is [after the first release](#oss-fuzz).

**Tests.** `tests/test_iso.py::test_pycdlib_directory_cycle_does_not_hang` (plain, Rock
Ridge and Joliet).

### Directory sources changed concurrently

**Property.** A directory source written by another process while archivey reads it
never lists or reads anything outside the root, never blocks on a swapped-in FIFO, and
never returns data the listing did not describe, except for the residuals below. This
is the one exception to "other local processes are trusted" (§1).

**Mechanism.** `internal/backends/directory_reader.py`:
- The walk lists with `lstat` and never follows a symlink or junction. On POSIX, where
  the descriptor walk is available (`_SCAN_BY_FD`), `_open_listed_directory` opens each
  subdirectory with `O_NOFOLLOW | O_DIRECTORY`, checks it against the
  `(st_dev, st_ino)` its parent's scan recorded, and scans and `lstat`s through that
  descriptor, so a directory swapped for a symlink before its scan, or reached through a
  swapped parent, fails the listing with `OSError(ESTALE)`. A directory listed with no
  identity is opened one component at a time from the root instead, each with
  `O_NOFOLLOW`, so a swapped parent fails there too.
- `_open_listed_file` opens each path component with `O_NOFOLLOW` relative to its
  parent's descriptor and the file with `O_NOFOLLOW | O_NONBLOCK`, so a symlink on the
  path fails with `ELOOP` and a FIFO does not block. It then `fstat`s the handle and
  raises `OSError(ESTALE)` (`_changed_since_listing`) for anything that is not a regular
  file with the listed identity and size. A listed size of 0 is exempt (procfs, sysfs).
- A member with no identity (`st_ino` 0: some FUSE and network mounts, or a Windows
  path the identity stat could not reach) is checked on type and size, plus, on Windows,
  a reparse-point check on its final component.

**Residual.** A same-size rewrite in place, or a change after the open, reads the
listed file's new content inside the root. A same-size replacement of an identity-less
member could read a same-size hardlink to a file elsewhere on that mount, on a
filesystem with hard links but no stable inodes. Windows is weaker and
[accepted](#windows-directory-sources). Public in extracting.md §Trust boundaries.

**Tests.**
`tests/test_directory.py::test_a_file_swapped_for_a_symlink_after_listing_is_refused`,
`::test_a_directory_swapped_for_a_symlink_after_listing_is_refused`,
`::test_a_file_replaced_after_listing_is_refused`,
`::test_a_file_swapped_for_a_fifo_after_listing_is_refused_without_blocking`,
`::test_a_file_resized_after_listing_is_refused`,
`::test_an_identityless_member_that_is_now_a_reparse_point_is_refused`,
`::test_a_directory_swapped_for_a_symlink_before_its_scan_is_refused`,
`::test_a_parent_swapped_for_a_symlink_before_a_subdirectory_scan_is_refused`,
`::test_a_parent_swap_is_refused_on_a_filesystem_without_identities`. Handbook:
[`formats/directory.md`](formats/directory.md) §2.3, §4.

### Attacker bytes reaching a terminal are inert

**Property.** Archive-derived text in any archivey message or CLI output cannot move the
cursor, erase a line or otherwise author what the operator sees. A name like
`README\x1b[2K\rSUCCESS.txt` renders escaped.

**Mechanism.** Text is escaped where it becomes a message, not where it is displayed.
`ArchiveyError`, `ArchiveyUsageError` and `Diagnostic` escape `message` at construction
with `terminal.py` `escape_control_chars`. We escape there, not in a logging formatter,
because the likeliest route to a terminal has no handler: an uncaught exception whose
traceback ends in `str(exc)`, or `print(exc)` in embedding code. A formatter would also
double-escape an already escaped message.
- `escape_control_chars` delegates to `repr`, whose escape set is exactly
  `not str.isprintable()`, escapes backslash itself, and renders a surrogateescaped byte
  as its octet. The promise is inertness, not unique recovery: `U+009B` and byte `0x9B`
  both render `\x9b`.
- Escape exactly once. Message sites delimit names with `terminal.quoted()`, not `!r`,
  and embed a caught error with `raw_message_of()`. Library `logger.*` calls are the
  inverse: records are not escaped, so `%r` is what makes an interpolated name inert
  there and must stay.
- Paths in messages are rendered `/`-separated first (`terminal.display_path`), so the
  escape has no native separator to double.
- CLI print sites go through `cli/format.py` `escape_member_name`, `escape_path` or
  `format_error_detail`, which escapes only non-archivey exceptions, since archivey's
  arrive escaped. `archivey info` escapes every value, including a ZIP comment on
  stdout.
- Structured fields stay raw for callers acting on values: `archive_name`,
  `member_name`, `source_format`, `Diagnostic.context`, and library log records.

**Residual.** The print-site sweep follows a local name to its assignments and a
same-module helper to its returns. What it cannot follow (an attribute, a cross-module
call, a `str` parameter) passes only through `_CLI_PRINT_ALLOWED`, where the stated
reason is trusted. `cli/main.py` `_format_os_error` is checked only by its own tests,
and tqdm's `desc` (escaped) is outside the sweep.

**Tests.** `tests/test_escaping.py` (primitive, message sites,
`test_no_message_site_interpolates_an_archive_derived_name_with_repr`,
`test_library_log_sites_still_escape_interpolated_names`,
`test_cli_print_sites_escape_what_they_print`,
`test_cli_does_not_escape_a_native_path`); `tests/test_cli.py::test_extract_escapes_*`,
`test_extract_summary_escapes_*`, `test_hoist_escapes_*`, `test_info_escapes_*`,
`test_missing_archive_name_is_escaped_once`. The cross-platform CLI tests use U+2028;
the ANSI/CR spoof is Unix-only, since NTFS refuses control bytes in names.

### Detection is bounded and says when it guessed

**Property.** `detect_format` does a bounded amount of work per call, and a content
probe that cannot confirm what it found says so rather than presenting a fabricated
member as fact.

**Mechanism.**
- `detection_cost.py` `DetectionBudget` bounds prefix, far, scan and decode bytes per
  call. `max_decode_input` is one allowance every decoding tier draws on (content
  probes, their whole-source completion check, the inner-TAR probe); a tier the
  remaining allowance cannot cover does not run. Output is bounded per probe by the
  codec's drain. A per-candidate cap cannot bound the aggregate: 2 MiB of back-to-back
  gzip decoys holds 209,715 valid headers, and decoding each to 64 KiB is 1.3 s and
  683-fold amplification. `DetectionCostReceipt` reports what was spent.
- The candidate search is linear in the window: `internal/sfx.py` `_EarliestFinder`
  carries each needle's next position forward.
- Brotli has no magic, so it is found by a content probe, which without gates accepted
  about 8% of random data. The probe rejects a first meta-block larger than a
  known-length source, a fully visible source that does not decode to completion, and
  later overruns or trailing bytes found by a bounded block-chain walk. It decodes the
  whole 4 KiB prefix (256 bytes let 7 of 800 Perl modules through; 4,096 let none),
  and re-checks a hit against the whole source up to `completion_window_bytes` (64 KiB
  under `BALANCED`, off under `FAST`). Probe-only confidence is `GUESS` for the
  uncompressed or metadata-first class; a later decode failure sets
  `format_unconfirmed=True` and emits `PROBE_FORMAT_UNCONFIRMED`. OLE compound
  files are not probed: their signature stops the probes. Other structured look-alikes
  (COFF objects, MP3s whose ID3 tag starts with padding) can still be claimed, and stamp
  the same way.

**Residual.** Measured with the 256-byte sample on a 150,623-file `/usr` tree: 29
fabricated claims (0.019%), 0 of them without a signal. That is the baseline for the
next census. A fabricated listing is [accepted](#a-probe-can-fabricate-a-member).
The aggregate bound [re-opens](#detection-decoding-scan-candidates) when a tier starts
decoding scan candidates.

**Tests.** `tests/test_brotli_framing_gate.py`, `tests/test_detection_workspace.py` (the
`*budget*` and receipt tests), the Atheris `detect_format` target. Investigation:
[`investigations/brotli-content-probe-results.md`](investigations/brotli-content-probe-results.md).

### External programs get a fixed command line

**Property.** No archive-controlled string reaches an external program as an option, and
the program that runs is one whose behaviour we have characterized.

**Mechanism.** `rar_decompressor="auto"` (the default; maintainer 2026-09-26, "auto is
default") takes RARLAB `unrar` or `rar` when installed and `unar` otherwise, decided once
at open. A read `unar` refuses is not retried with `unrar`, and reads `unar` gets wrong
are refused before it runs. `unrar-free`, `7z` and `bsdtar` are never used: their
failures (empty files with a success exit, a missing plugin, gigabytes written for a
stored member) are invisible to the caller. A non-RARLAB `unrar` raises
`PackageNotInstalledError`. Both paths use a banner probe with a timeout and a
stat-keyed cache. `internal/backends/rar_unrar.py` passes a member name only inside a
`-n./<name>` include-mask switch, so a leading `-` cannot become an option, narrows
every `*` to `?` (`_unrar_mask_for`) because `unrar`'s matcher backtracks exponentially
on a hostile mask, and writes the password to stdin. `internal/external/unar.py` ends
argv with `--` and the absolute archive path, names members by entry index, and puts the
password as its own item after `-p`. ADR
[0002](decisions/0002-native-rar-metadata-unrar-data.md),
[`investigations/alternative-rar-decompressors.md`](investigations/alternative-rar-decompressors.md).

**Residual.** The program is found on the process `PATH` and is part of the deployment's
trust boundary. `unar`'s password is [visible to other users](#the-unar-password-is-on-its-command-line).

### Concurrent member streams are race-free where declared

**Property.** On a reader declaring `MemberStreams.CONCURRENT`, after random-access
materialization, concurrent `open()` and independent use of different member streams is
data-race-free without the GIL. Otherwise a second overlapping open raises
`ArchiveyUsageError`, so accidental sharing fails fast. Iteration, extraction,
`stream_members()` and close are single-owner. Stream leases defer backend teardown
until the last stream closes, because an accelerator must close before it is finalized
([`investigations/rapidgzip-upstream-report.md`](investigations/rapidgzip-upstream-report.md)
§6).

**Tests.** The required `free-threaded-concurrency` job (Linux CPython 3.13t,
`.github/workflows/ci.yml`); optional backends are not claimed until a job runs them.
Scope: [`docs/support-matrix.md`](../docs/support-matrix.md); design:
[`investigations/parallel-reader.md`](investigations/parallel-reader.md) §4.

## 4. Accepted non-guarantees

Each of these was chosen, and each has a public line so it is not reported as a
vulnerability. Most are in
[extracting.md §Known and accepted limits](../docs/extracting.md#known-and-accepted-limits).

### No CPU or wall-clock bound

Every limit caps bytes, entries or key-derivation rounds, not time. Worst cases we know:
a 7z BCJ2 `main` stream made only of branch candidates decodes at 1.8 MB/s in pure
Python (26 MB/s on a real executable), and LZMA2 compresses that stream to almost
nothing; the key-derivation budget allows about half a minute; a late-CRC 7z folder
costs folder size times candidates. A "work per output byte" limit for BCJ2 was
considered and not added: no other codec has one, and `ExtractionLimits` still bounds
the amount, only the rate is lower. Callers who need a time bound run archivey in a
worker they can kill. Public: [known and accepted limits](../docs/extracting.md#known-and-accepted-limits).

### A native decoder crash or MemoryError

The stdlib `zlib`, `bz2` and `lzma`, pyppmd below 16 MiB (handed the member whole, which
avoids the input pattern known to crash it) and the bzip2 accelerator run in-process. A
crash nobody has found yet would abort the process. The bzip2 accelerator stays
in-process because no crash has been seen in it. `MemoryError` is not translated, so
running out of memory is never mistaken for a damaged archive; the limits above exist to
keep a hostile archive from getting that far. Public:
[known and accepted limits](../docs/extracting.md#known-and-accepted-limits).

### Accelerators on by default, and unbounded in time

`AcceleratorMode.AUTO` engages the `[seekable]` accelerators when installed and a caller
asks for seeking. They are third-party C++ that can busy-loop on crafted input, in a
thread no Python timeout can cleanly interrupt. The gzip-family decoder runs in a child
process, so an abort costs only the member, but a loop there has no read timeout. The
Atheris accelerator targets fuzz them ([Parsers survive hostile bytes](#parsers-survive-hostile-bytes))
under libFuzzer's timeout; a loop they have not found is still unbounded at run time. Callers
with a hard latency budget set them to `OFF`. Public:
[known and accepted limits](../docs/extracting.md#known-and-accepted-limits) and
[hardening notes](../docs/extracting.md#hardening-notes-for-callers).

### A seek trusts the file's own index

A cold seek into a `.xz` or `.lz` goes where the file's index says (XZ block records, or
lzip member trailers), and both are attacker-controlled. An index consistent with itself
but not with the data serves bytes from the wrong unit, and `try_get_size()` /
`member.size` report the index's total, with no error. Accepted, ruled by davi on
2026-09-23 (PR #407 review round 1): a unit's real end is known only by decompressing
it, and avoiding that is the index's purpose. Decoding from the start before the first
cold seek would remove fast random access for every honest file, and xz's and lzip's
own tools trust their indexes the same way.

What the design still catches: a seek landing inside the misdescribed region resumes
from a checked point before it and raises when the decode reaches the end of the lying
unit (the member's trailer for lzip, the stream end for xz); bytes returned before that
are correct for their offsets. A seek past the lie followed by a read to the end is not
caught, since every later unit is genuine. A diagnostic for an ambiguous trailer walk was
considered and not taken: an attacker who controls the trailers can avoid it. Public:
[the integrity guarantee](../docs/errors-and-diagnostics.md#the-integrity-guarantee) and
[known and accepted limits](../docs/extracting.md#known-and-accepted-limits). Tests:
`tests/test_seekable_streams.py::test_lzip_cold_seek_trusts_a_self_consistent_trailer_chain`,
`::test_xz_cold_seek_trusts_a_self_consistent_block_index`.

### Windows directory sources

Windows has no `O_NOFOLLOW` and no descriptor-based `scandir`, so the walk scans
subdirectories by path: a subdirectory swapped for a junction or symlink between its
parent's scan and its own lists the target's entries. Reads are still refused where the
listing recorded an identity. For an identity-less member the reparse check covers only
the leaf and runs after the open, so a junction swapped in above it, or a swap racing
the check, reads a same-size file outside the root. Both inferred from the code, not
measured. On Python 3.11, `DirEntry.is_junction()` does not exist, so a junction may be
walked ([`formats/directory.md`](formats/directory.md) §7). Public:
[known and accepted limits](../docs/extracting.md#known-and-accepted-limits).

### Nested-archive amplification

Recursion into archives inside archives is caller-driven, so a zip quine loops only if
the caller loops. The bomb tracker measures one archive, so a zip of zips can pass the
budget one level at a time. The caller bounds depth and total size. Public:
[extracting.md §Limits](../docs/extracting.md#limits) and the "Nested archives" row in
[§Names change on disk](../docs/extracting.md#names-change-on-disk).

### Reads have no output bound

`read()` and `open()` return whatever the member decodes to; `ExtractionLimits` apply to
`extract` only. Chunk untrusted payloads. Public:
[extracting.md §Limits](../docs/extracting.md#limits) and
[`docs/gotchas.md`](../docs/gotchas.md).

### A probe can fabricate a member

When a content probe's identification is wrong (see
[detection](#detection-is-bounded-and-says-when-it-guessed)), the listing shows one
fabricated member, a full read raises, and up to 64 KiB of fabricated output may be
returned before the raise. It is never a silent success, and it carries
`PROBE_FORMAT_UNCONFIRMED`. Public: [`docs/formats.md`](../docs/formats.md) §Detection
and [`docs/gotchas.md`](../docs/gotchas.md).

### The unar password is on its command line

`unar` takes a password only on argv, so under `unar` it is visible to other local users
in `ps` and `/proc/<pid>/cmdline` while the process runs. Accepted by the maintainer
(2026-09-26, "fine in most cases"). Because `"auto"` is the default, a caller without
RARLAB installed gets this without choosing it. `RarDecompressor.UNRAR` rules it out.
Public: [hardening notes](../docs/extracting.md#hardening-notes-for-callers).

## 5. Open design gaps

### 7z password confirmation on a late CRC

The shapes [confirmation](#7z-password-confirmation) cannot settle early, all with
several candidates: a 7z Copy, PPMd, Brotli or filter-only chain whose only CRC is at the
folder end; any ZIP, 7z or RAR3/4 unit where more than one candidate survives the bounded
probe, which then walks to the end anchor once per survivor; and a stored RAR3/4 member,
decrypted in full once per candidate. Each costs time proportional to the unit, once per
candidate, and only when the caller passes a list. For 7z, the OpenSpec change
`sevenzip-aes-tail-key-check` adds an O(1) check on the AES padding at the end of the
packed stream, which settles it for the archives with at least 4 padding bytes. Not
implemented (its tasks are open).

### Detection decoding scan candidates

No detection tier decodes scan candidates today, so the 683-fold amplification in
[detection](#detection-is-bounded-and-says-when-it-guessed) is not reachable. A tier
that does (makeself compressor needles after a `#!` stub, planned after 0.2.0) must draw
on the shared `max_decode_input` allowance and be measured before this is settled.

### OSS-Fuzz

Onboarding comes after the first release. The bar for calling archivey safe (threat
model, adversarial corpus, coverage-guided fuzzing, disclosure process) is met without
it.

### Bounded recursion helper

"Index my backups", the founding use case, recurses into nested archives. A recipe or
helper for bounded recursive processing does not exist yet.

### Metadata fidelity

PAX xattrs survive only in `extra["tar.pax_headers"]`; ACLs, macOS resource forks and
NTFS ADS are not read. Promoting them to first-class fields on read is additive.
Applying them at extraction interacts with the policies. Full fidelity binds when
writing lands (possibly after 1.0), and must be a day-one decision of the writing spec
([`IDEAS.md`](IDEAS.md)).

## 6. Index of old register ids

| Old id | Title | Now |
| --- | --- | --- |
| O1 | Listing-time metadata bombs | [Listing](#listing); unbounded reads: [Reads have no output bound](#reads-have-no-output-bound); glob reads: [RAR reads by glob-named member](#rar-reads-by-glob-named-member) |
| O2 | Case and Unicode-normalization collisions | [Names are safe](#names-are-safe-on-the-target-filesystem) |
| O3 | Windows reserved names, trailing dots and spaces | [Names are safe](#names-are-safe-on-the-target-filesystem) |
| O4 | NTFS alternate data streams | [Names are safe](#names-are-safe-on-the-target-filesystem) |
| O5 | Fuzzing | [Parsers survive hostile bytes](#parsers-survive-hostile-bytes), [OSS-Fuzz](#oss-fuzz) |
| O5 (accelerator hang) | | [Accelerators on by default](#accelerators-on-by-default-and-unbounded-in-time) |
| O5 (pycdlib cycle) | | [Parsers survive hostile bytes](#parsers-survive-hostile-bytes) |
| O5 (`"."` root poisoning) | | [Extraction stays in the destination](#extraction-stays-in-the-destination) |
| O6 | Nested-archive amplification | [Nested-archive amplification](#nested-archive-amplification), [Bounded recursion helper](#bounded-recursion-helper) |
| O7 | Names the filesystem cannot represent | [Names are safe](#names-are-safe-on-the-target-filesystem) |
| O8 | 7z wrong header password gives an empty archive | [Integrity verdicts](#integrity-verdicts-come-from-reads) |
| O9 | Attacker bytes reaching the terminal | [Terminal](#attacker-bytes-reaching-a-terminal-are-inert) |
| O10 | Content probe fabricates a member | [Detection](#detection-is-bounded-and-says-when-it-guessed), [A probe can fabricate a member](#a-probe-can-fabricate-a-member) |
| O11 | Detection-time decode work | [Detection](#detection-is-bounded-and-says-when-it-guessed), [Detection decoding scan candidates](#detection-decoding-scan-candidates) |
| O12 | 7z password confirmation | [7z password confirmation](#7z-password-confirmation), [late CRC gap](#7z-password-confirmation-on-a-late-crc) |
| O13 | 7z `NumUnpackStreams` allocation | [Listing](#listing) |
| O14 | 7z encoded-header nesting | [Listing](#listing) |
| O15 | Tar extended header sized an allocation | [Allocations sized by a header field](#allocations-sized-by-a-header-field) |
| O16 | ISO directory record sized an allocation | [Allocations sized by a header field](#allocations-sized-by-a-header-field) |
| O17 | Seek trusts the xz/lzip index | [A seek trusts the file's own index](#a-seek-trusts-the-files-own-index) |
| O18 | Archive chooses the key-derivation cost | [Key derivation](#key-derivation) |
| O19 | Data-stored symlink target sized an allocation | [Listing](#listing) |
| O20 | 7z BCJ2 in pure Python | CPU: [No CPU or wall-clock bound](#no-cpu-or-wall-clock-bound); memory: [Decoder memory](#decoder-memory) |
| O21 | Directory source changed between listing and reading | [Directory sources](#directory-sources-changed-concurrently), [Windows directory sources](#windows-directory-sources) |
| O22 | A later member turns an extracted symlink into an escape | [A later member cannot make an extracted symlink escape](#a-later-member-cannot-make-an-extracted-symlink-escape) |
| C1 | RAR data through an external program | [External programs](#external-programs-get-a-fixed-command-line), [unar password](#the-unar-password-is-on-its-command-line) |
| C2 | Warnings that should be data | [Errors are typed and honest](#errors-are-typed-and-honest) |
| C3 | Metadata fidelity | [Metadata fidelity](#metadata-fidelity) |
| C4 | Free-threaded Python | [Concurrent member streams](#concurrent-member-streams-are-race-free-where-declared) |
