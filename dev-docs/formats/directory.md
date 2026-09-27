# Directory

Current maintainer truth for the directory pseudo-backend. `open_archive()` on a directory
path returns a reader over the live filesystem tree under it, with the same API and the
same stream contract as a real archive. There is no file to parse: the "format" is
whatever `os.scandir` and `lstat` report, read at the moment the walk reaches each entry,
and the data is whatever the file holds at the moment it is opened. Most of what is
peculiar here follows from that. Registers keep the status; this page states the behaviour
and links the row.

## At a glance

| | |
| --- | --- |
| Read | Yes. `internal/backends/directory_reader.py`, a walk over `os.scandir` with one `lstat` per entry |
| Write | **Not shipped**, for any format (`PLAN.md` phase 9) |
| Source | A directory path only (`str` or `Path`). A symlink to a directory opens its target. No stream, no start offset |
| Listing cost | `REQUIRES_SCANNING`. `members_report_if_available()` is `None` until a pass has run, and `member_count` is always `None` |
| Access cost | `DIRECT`; `is_solid=False` |
| Stream capability | `SEEKABLE` in the receipt. A member stream is still forward-only and exclusive unless the caller declares `MemberStreams.SEEKABLE` / `CONCURRENT`, as for every format |
| Core dependencies | None |
| Encryption | None. `password=` is accepted and dropped with `PASSWORD_ARGUMENT_UNUSED` |
| Name encoding | The filesystem's own. `encoding=` is dropped with `ENCODING_ARGUMENT_UNUSED` |
| Refuses | A `format=` other than `DIRECTORY` on a directory path (`ArchiveyUsageError`) · opening a directory or an `OTHER` member (FIFO, socket, device) · writing. `OTHER` members list, and the shared filter skips them at extraction |

**Four things a reader might expect and will not find.** Nothing ties a member's data to
its listing: a file that changed, shrank, grew or was replaced by a symlink after the walk
reads as it is now, with no error (§1, §4). A read never uses the filesystem to resolve a
symlink member; it resolves the target inside the listed tree, so a link to a path outside
the root fails with `LinkTargetNotFoundError` even though the file exists (§2.3). The walk
does not stay on one filesystem: it descends into mount points, including `/proc` and
`/sys`, whose files list `size` 0 and then read their content (§3). And on Linux `created`
is always `None`, because `os.stat` exposes no birth time there (§2.2).

## 1. Shape

Three properties generate most of this page.

```
root/                     walk order (depth-first preorder, per-directory name order)
├── a.txt       FILE      1. a.txt
├── b.txt  ─┐   HARDLINK  2. b.txt      → link_target "a.txt" (same st_dev, st_ino)
├── link   ─┼─▶ SYMLINK   3. link       → link_target as readlink() returned it
├── fifo    │   OTHER     4. fifo
└── sub/    │   DIRECTORY 5. sub/
    └── c.txt   FILE      6. sub/c.txt
```

**The tree is live, and the listing is a snapshot of it at walk time.** Every other
backend reads bytes that do not change between listing and reading. Here nothing is
frozen. The walk reads one directory at a time and types each entry from its own `lstat`,
so an entry that vanishes between `scandir` and `lstat` (or `readlink`) is skipped with a
`SCAN_ENTRY_VANISHED` or `SCAN_DIRECTORY_VANISHED` diagnostic and the walk goes on
(§2.2). Every other `OSError`, a permission error on a subdirectory included, fails the
whole listing, because a listing with a hole in it would look complete. A member's
`size`, `mode` and times are what `lstat` said when the walk passed. Reading opens the
path again later and gets whatever is there then (§2.3). This is the same gap a TAR walk
has between a header and its data, except that a TAR's bytes cannot change in it.

**There is no index; the walk is the listing.** Listing cost is `REQUIRES_SCANNING`, like
a plain TAR, and for the same reason: nothing short of the walk knows what is in the tree.
So `member_count` is `None` and `members_report_if_available()` returns `None` before a
pass, and the walk runs once under the materialization election instead of on every peek.
Each file is independently addressable once listed, so random opens are `DIRECT`. The
walk uses an explicit stack, not recursion, so a tree deeper than the interpreter's
recursion limit lists like any other.

**The API is the archive API, no more lenient.** The backend exists so code written
against a directory behaves the same when pointed at a real archive. So a member stream
is forward-only and exclusive by default even though a real file could seek and share,
and `seek()` raises `io.UnsupportedOperation` until the caller declares
`MemberStreams.SEEKABLE`. Hardlinks list as a tar lists them (one `FILE`, later names
`HARDLINK`), symlink targets resolve inside the listed tree, and extraction runs through
the same filter. Where the filesystem could do more, the reader does what an archive
would.

## 2. The pipeline here

### 2.1 Identify

Nothing is read. `ArchiveSource.for_path` checks `path.is_dir()`, which follows a symlink,
and a directory source short-circuits detection: `open_archive` resolves the format to
`DIRECTORY` before any backend or probe runs, and `detect_format()` on a directory path
returns `DIRECTORY` with `CERTAIN` confidence, `detected_by="directory"` and a zero cost
receipt (`directory_format_info()` in `internal/detection.py`). The backend declares no
magic and no extension.

An explicit `format=` other than `DIRECTORY` on a directory path raises
`ArchiveyUsageError` rather than being silently overruled; `format=DIRECTORY` is accepted.
`open_stream()` refuses a directory path with a message that points to `open_archive()`,
rather than "not found".

### 2.2 Open and list

**Who does the work.** `DirectoryReader._iter_members`, which pops subdirectories off a
stack, and `_scan_level`, which lists one directory. Opening does nothing but record the
root; the first listing call runs the walk.

**Order.** At each level, `scandir` output is sorted by name. The non-directory entries of
a level come first, then each subdirectory's own member followed by its whole subtree.
Subdirectories are pushed in reverse so they pop in name order. The order is therefore a
depth-first preorder that does not depend on the filesystem's own directory order, which
is what makes the choice of hardlink "first name" deterministic.

**Typing.** Each entry's type comes from `entry.stat(follow_symlinks=False)`, not from the
`DirEntry` snapshot, so an entry replaced between `scandir` and `lstat` is typed as what is
there now, and `readlink` only runs on something that is a link.

| On disk | Member |
| --- | --- |
| Regular file, one name in the tree | `FILE`, `size` from `st_size` |
| Regular file, a later name of the same `(st_dev, st_ino)` | `HARDLINK`, `link_target` the first name, `size` `None` |
| Symlink | `SYMLINK`, `link_target` the raw `readlink()` value, not walked through |
| Windows junction | `SYMLINK` with `extra["is_junction"]`, not walked through |
| Directory | `DIRECTORY`, name with a trailing `/`, walked |
| FIFO, socket, device | `OTHER` |

A file with `st_nlink > 1` whose other names are all outside the root lists as `FILE`. So
does every file on a filesystem that reports `st_ino` 0 (Windows `scandir` data, some FUSE
and network mounts), because grouping on inode 0 would link unrelated files. On Windows,
`DirEntry.stat` serves `scandir`'s cached data, where `st_ino`, `st_dev` and `st_nlink` are
all zero, so regular files get one fresh `lstat` for their identity. If that `lstat` fails
with `FileNotFoundError` (a path past `MAX_PATH` without long-path support), the file keeps
the cached data and lists as `FILE`, not as vanished.

**Races.** `FileNotFoundError` from `scandir` skips that directory with
`SCAN_DIRECTORY_VANISHED`; from `lstat` or `readlink` it skips that entry with
`SCAN_ENTRY_VANISHED`. The diagnostic carries the relative path and the entry kind, never
a `DirEntry`, `Path` or exception, and attaches to the reader, not to a member. Under a
`RAISE` policy the diagnostic halts the walk. Every other `OSError` propagates unchanged.
On Windows the cached stat means an entry replaced (not removed) in the window can still
fail the listing; the code comment says so.

**Metadata mapping.**

| Field | Source |
| --- | --- |
| `name`, `raw_name` | Joined from `DirEntry.name` with `/`. Already normalized: no `.`, `..` or leading `/` can occur. On POSIX, bytes that are not UTF-8 arrive as surrogate escapes (`bad\udcff.txt`), and `raw_name` is the original bytes. On POSIX a `\` in a name is a literal character and is kept |
| `link_target` | `os.readlink()`. On Windows `\` becomes `/` so targets share the member-name namespace |
| `size`, `compressed_size` | `st_size`, for `FILE` only; both are the same number |
| `compression` | `()`; nothing is compressed |
| `modified`, `accessed` | `st_mtime`, `st_atime` |
| `created` | `st_birthtime` where `os.stat` has it (macOS, BSD, Windows), else `None`. Never `st_ctime` |
| `ctime` | `st_ctime`, except on Windows, where `st_ctime` was the creation time before Python 3.12 |
| `mode` | `S_IMODE(st_mode)`: permission bits only, the type is in `type` |
| `uid`, `gid`, `uname`, `gname` | `st_uid`, `st_gid`; names from `pwd` / `grp`, cached per reader, `None` when the lookup fails or the module is missing (Windows) |
| `extra` | `is_junction` for a junction; `is_reparse_point` for every symlink on Windows, where a symlink is always a reparse point |

A timestamp outside `datetime`'s range, which a network or FUSE filesystem can report,
lists as `None` rather than failing the walk. A pre-1970 time is a real date on every
platform.

**Limits.** The shared `ListingLimits` apply: a tree with more entries than `max_members`
raises `ResourceLimitError` when listed, as for any archive. `stream_members()` does not
enforce them, as elsewhere.

### 2.3 Member data

`_open_member` opens `root / member.name` with plain `open(…, "rb")` and wraps it in the
same `ArchiveStream` every backend returns, advertising the listed `size`. There is no
translator, so a genuine `OSError` (a permission error, a vanished file) propagates
unchanged.

That open is by path, at read time, and follows whatever is on disk then. Three
consequences, all measured:

- A file that changed after the walk reads its new content. A file listed at 5 bytes and
  grown to 13 reads 13 bytes; one listed at 10 and cut to 2 reads 2 bytes with no
  `TruncatedError`. `stream.size` still reports the listed number. No check compares them.
- A file or directory replaced by a symlink after the walk is followed. Opening a listed
  `a.txt` that is now a symlink to a file outside the root returns that file's bytes, and
  so does opening `sub/b.txt` after `sub/` became a symlink (§4).
- A file replaced by a FIFO after the walk blocks `open()` until something writes to the
  FIFO.

A `SYMLINK` member is resolved the archive way, not the filesystem way: `open()` follows
`link_target` to another member of the listing. A link to `t` inside the root reads `t`;
a link to an absolute path or out through `..` raises `LinkTargetNotFoundError`, whether
or not the target exists on disk. A link to a directory member raises
`ArchiveyUsageError`, and a name through a directory symlink (`dirlink/f`) is not a member
at all. A `HARDLINK` reads its first name's file.

### 2.4 Extract

Nothing is directory-specific: the shared extractor and filter run over the listing as
they would over a tar. The format-shaped outcomes are these.

- **Hardlinks** extract as hardlinks. With `streaming=True`, a `HARDLINK` whose first name
  a selector or filter left out fails with `ExtractionError`, because the forward-only
  reader cannot go back for the data; non-streaming extraction is unaffected.
- **Symlinks** whose target leaves the destination (absolute, or out through `..`) are
  skipped by the filter, as for any archive. So a tree that points outside itself does not
  extract those links.
- **`OTHER` members** are skipped with "Special file (device/FIFO/socket) not allowed".
- **Undecodable names** from POSIX extract with each escaped byte as `%XX` (`bad%FF.txt`),
  the shared filter's rule for surrogate escapes.
- **Limits** apply with `compressed_size` equal to the listed size, so the per-member
  ratio is 1:1 for a file that did not change. A file that grew past `max_ratio` times its
  listed size since the walk fails with a `ResourceLimitError` that calls it a
  decompression ratio (§5).
- **A destination inside the root** is part of the tree being walked (§5).

### 2.5 Write

Not shipped for any format. The `format-directory` spec states that a directory reader
feeds `writer.add_members(reader)` in one forward pass without buffering the tree; that is
a requirement for the future writer, and nothing tests it today.

## 3. In the wild

There are no producers; there are filesystems, and each reports `lstat` differently.

- **ext4, btrfs, xfs, APFS, NTFS** report full identity, so hardlinks group and modes
  round-trip. Linux reports no birth time through `os.stat`, so `created` is `None` there
  and set on macOS and Windows.
- **Windows.** `scandir` data has no identity, hence the per-file `lstat` (§2.2). Every
  symlink is a reparse point and is flagged so. Junctions are detected with
  `DirEntry.is_junction()`, which exists from Python 3.12; on 3.11 the check returns
  `False` (§7). `readlink` targets come back with `\` and are converted. Paths past
  `MAX_PATH` without long-path support fail the identity `lstat`, and those files list as
  `FILE`.
- **Some FUSE and network mounts** report `st_ino` 0, so their hardlinks list as separate
  files. They can also report timestamps outside `datetime`'s range, which list as `None`.
- **procfs and sysfs** list regular files with `st_size` 0 whose read returns content
  (`/proc/sys/kernel/random/uuid` lists 0 and reads 37 bytes). The walk does not check
  `st_dev` against the root's, so a root above a mount point descends into it.
- **A tree an archive was extracted into** is the common input. It carries whatever that
  archive carried: symlinks pointing outside the root, deep nesting, names that are not
  UTF-8. The deep-tree case is why the walk is iterative.

## 4. Threat surface

The listing is safe to hand a hostile tree: the walk never follows a symlink or junction, so
it cannot loop (Windows on Python 3.11 aside, §7), and every name is built from real directory entries, so there is no `..`,
absolute path or empty component to normalize. The listing limits bound the member count
and the retained text. Extraction goes through the shared filter
([`threat-model.md`](../threat-model.md)).

What is specific to this backend is the gap between listing and reading, when someone
else can write to the tree while archivey reads it: an upload staging directory, a shared
drop folder. Whoever controls the tree can:

- **swap a listed file or directory for a symlink**, so a read returns a file outside the
  root that the caller never listed and may not be allowed to publish. The open follows
  it with no error (§2.3). Tracked internally.
- **swap a file for a FIFO**, so the read blocks until they write to it.
- **grow a file after the walk**, so a read returns more than the listed `size`; limits
  based on the listing do not bound it, and extraction's per-member ratio is the only
  backstop (§2.4).

A caller who reads a tree only it can write is not exposed to any of this.

## 5. Sharp edges

*Where it lives*: **format** — inherent, no implementation fixes it · **library** — an
upstream library's behaviour, fixable only there or by replacing it · **archivey** — ours.

| What you see | Where it lives | More |
| --- | --- | --- |
| A read returns a file from outside the root | **archivey** | A listed path replaced by a symlink after the walk is followed at open (§2.3, §4). Opening with `O_NOFOLLOW` and checking the identity against the listing would refuse it. Tracked internally |
| A read returns a different length from `member.size`, with no error | **archivey** | The open is by path and nothing compares the file with its listing (§2.3). Tracked internally |
| Extracting a file that grew since listing fails with "Decompression ratio … exceeds limit" | **archivey** | `compressed_size` is the listed size, so growth reads as a ratio. The error does not say the file changed. Tracked internally |
| Extracting into a folder inside the root adds that folder to the output (`out/out/`); with `streaming=True` it nests `out/out/out/…` until the path is too long | **archivey** | The walk reads the live tree, including what the extraction writes. Tracked internally |
| `open()` on a listed file hangs | **archivey** | The file was replaced by a FIFO after the walk (§2.3). Same fix as the symlink row |
| A symlink whose target exists on disk raises `LinkTargetNotFoundError` | **archivey** | By design: targets resolve inside the listed tree, as in an archive (§6) |
| A permission error in one subdirectory fails the whole listing | **archivey** | By design: a listing with a hole would look complete (§6) |
| A member vanished from the listing and a `SCAN_*_VANISHED` diagnostic says why | **format** | The tree changed during the walk (§1) |
| Files in `/proc` or `/sys` list `size` 0 and read content | **format** | The filesystem reports it; the walk crosses mount points (§3) |
| Hardlinked files list as separate `FILE`s | **format** | The filesystem reports `st_ino` 0, or the other names are outside the root (§2.2) |
| `created` is `None` on Linux | **library** | `os.stat` has no birth time on Linux |
| `seek()` fails on a real file, or a second open raises `ConcurrentAccessError` | **archivey** | By design: the archive contract, until `MemberStreams.SEEKABLE` / `CONCURRENT` is declared (§6) |
| `password=` or `encoding=` is accepted and has no effect | **archivey** | Dropped with `PASSWORD_ARGUMENT_UNUSED` / `ENCODING_ARGUMENT_UNUSED`; shared behaviour, not the directory's own |

## 6. Decisions

| Choice | Why | Rejected |
| --- | --- | --- |
| Hold member streams to the archive contract | Code developed against a directory must behave the same against a real archive; a lenient directory reader hides the bug until production | Exposing the file's own seek and sharing |
| Skip entries that vanish mid-walk with a diagnostic; propagate every other `OSError` | A live tree changing is a race, not an error. Dropping an unreadable subdirectory would present an incomplete listing as complete | Skipping every `OSError`; failing on a vanished entry |
| Type each entry from its own `lstat`, not the `scandir` snapshot | An entry replaced in the window is typed as what is there, and `readlink` runs only on a link | Trusting `DirEntry.is_symlink()` and friends |
| Never walk through a symlink or junction | The listing stays inside the root and cannot loop; a link is a member, as in an archive | Following links, with a visited set |
| Resolve symlink members inside the listing | Same as every archive backend, so a link out of the tree behaves as it would in a tar of the tree | Resolving through the filesystem, which reads outside the root |
| List later names of a hardlinked file as `HARDLINK` | A tar of the same tree does; converting the tree then stores the data once and keeps the link (PR #433) | Every name as its own `FILE`, which duplicated the data and lost the link |
| Sort each directory and walk depth-first with an explicit stack | The order, and so the hardlink "first name", does not depend on the filesystem; any depth lists (PR #428) | Filesystem order; recursion, which failed near 1 000 levels |
| `REQUIRES_SCANNING`, no upfront member list | There is no index; running the walk on every peek would be a hidden full scan | Claiming `INDEXED` because a directory feels indexed |
| `created` only from a birth time | `created` never holds `st_ctime`; the change time goes to `ctime` (PR #470) | Falling back to `st_ctime` |
| Refuse a conflicting `format=` on a directory path | Silently overruling it hands back a reader the caller did not ask for | Ignoring `format=` |

## 7. Open questions

- **Does a junction get walked on Windows with Python 3.11?** `DirEntry.is_junction()`
  arrived in 3.12, and `lstat` reports a junction as a directory, so on 3.11 the walk
  probably descends into it, and a junction pointing at an ancestor would loop until the
  path is too long. This is inferred from the code, not measured: the only junction test
  is skipped below 3.12, although CI runs a Windows 3.11 leg. Running that test there
  would answer it; if it fails, `st_reparse_tag` (available since 3.8) is the fix.
  Tracked internally.
- **What should a size that changed since listing do?** A diagnostic fits a live tree,
  where change is ordinary; a refusal fits a caller who treats the listing as a manifest.
  Until someone decides, the read returns the new length silently and extraction only
  notices past `max_ratio`. Tracked internally.

## 8. Verify

```bash
./scripts/test.sh tests/test_directory.py
./scripts/test.sh tests/test_corpus_sweep.py -k dir
```

| Claim | Pinned by |
| --- | --- |
| A directory path opens as `DIRECTORY`, as `str` or `Path`; a conflicting `format=` is refused | `tests/test_directory.py::test_open_directory_returns_reader`, `::test_open_directory_as_string`, `::test_explicit_directory_format_is_accepted`, `::test_conflicting_format_on_directory_raises` |
| `detect_format` answers `DIRECTORY` with a zero receipt | `tests/test_detection.py::test_detect_format_reports_directory_for_a_directory_path`, `::test_detect_format_directory_carries_a_zero_receipt` |
| Cost receipt, no upfront list | `tests/test_directory.py::test_cost_receipt`, `::test_members_report_if_available_returns_none_before_scan` |
| Walk order; any depth lists | `::test_non_dirs_listed_before_subdirs`, `::test_walk_order_is_depth_first_preorder`, `::test_tree_deeper_than_recursion_limit_lists` |
| Metadata: sizes, times, `ctime` not on Windows, mode | `::test_members_file_sizes`, `::test_members_have_modified_timestamp`, `::test_members_ctime_is_st_ctime_except_on_windows`, `::test_members_have_mode`, `::test_stat_datetime_guards_out_of_range_values` |
| Symlinks list as `SYMLINK` and resolve inside the tree | `::test_symlink_member_type`, `::test_symlink_link_target_member_resolved`, `::test_open_symlink_follows_to_real_content` |
| A junction is flagged and not walked (Windows, 3.12+) | `::test_windows_junction_detected_and_not_traversed` |
| Races skip with a diagnostic; a genuine error fails the listing | `::test_subdirectory_vanishing_mid_walk_is_skipped`, `::test_symlink_vanishing_before_readlink_is_skipped`, `::test_symlink_replaced_by_file_mid_scan_lists_as_file`, `::test_unreadable_subdirectory_fails_listing`; `tests/test_diagnostics.py::test_directory_scan_race_diagnostic` |
| Hardlinks: first name `FILE`, later `HARDLINK`; outside names and inode 0 stay `FILE`; the Windows identity `lstat` | `tests/test_directory.py::test_hardlinked_names_list_as_hardlink_to_the_first`, `::test_link_count_from_outside_the_tree_is_a_plain_file`, `::test_hardlinked_directory_extracts_both_names`, `::test_zero_inode_is_no_identity`, `::test_identity_stat_path_failure_keeps_the_file`, `::test_identity_stat_genuine_error_propagates` |
| Streaming extraction fails a link whose first name was filtered out | `::test_streaming_extract_with_first_name_filtered_out_fails_the_link` |
| Password dropped with a diagnostic | `::test_password_is_accepted_and_recorded` |
| Same reader surface and streaming mode as the archive backends | `tests/test_review_simplicity_consistency.py::test_reader_surface_is_uniform_across_formats`, `::test_streaming_mode_is_uniform_across_formats` |
| Concurrent reads | `tests/test_concurrent_multithread.py::test_multithread_directory_open_read` |
| Cross-format equivalence, including `hardlinks-walk-order` | `tests/test_corpus_sweep.py` (`dir` cells from `tests/sample_archives.py`) |

**Nothing pins §2.3's open-time behaviour** (a changed, swapped or FIFO-replaced file after
listing), nor the extraction-into-the-root behaviour in §5. To see them, list a tree,
change it, then read:

```python
with archivey.open_archive(root) as r:
    m = r.get("a.txt")
    os.remove(root / "a.txt"); os.symlink(outside_file, root / "a.txt")
    r.read(m)  # returns outside_file's bytes
```

**Building fixtures.** The tests build trees in `tmp_path`; nothing is checked in. A
hardlink is `os.link`, a FIFO `os.mkfifo`, a name that is not UTF-8 `open(os.path.join(
os.fsencode(root), b"bad\xff"), "wb")`. Races are simulated by wrapping `os.scandir` or
the `_identity_stat` seam rather than by timing. A junction needs Windows: `cmd /c mklink
/J` makes one without admin rights.

## 9. References

- Python `os.scandir`, `os.DirEntry` (`is_junction` since 3.12), `os.stat_result`
  (`st_birthtime` availability, `st_reparse_tag` since 3.8), `os.readlink`
- Windows reparse points: `FILE_ATTRIBUTE_REPARSE_POINT`, `IO_REPARSE_TAG_MOUNT_POINT`,
  `IO_REPARSE_TAG_SYMLINK` (winnt.h); the archive-side parser is
  `internal/windows_reparse.py`
- Specs: [`format-directory`](../../openspec/specs/format-directory/spec.md) ·
  [`archive-reading`](../../openspec/specs/archive-reading/spec.md) (the uniform stream
  contract)
- Code: `internal/backends/directory_reader.py` · `internal/source.py` (`for_path`) ·
  `internal/detection.py` (`directory_format_info`) · `core.py` (the `format=` refusal)
- Registers: [`threat-model.md`](../threat-model.md) (the shared extraction filter) ·
  [`open-issues.md`](../open-issues.md) P8 (the `format=` refusal, closed)
- Handbook: [`tar.md`](tar.md) (the hardlink shape this reader copies, and the other
  `REQUIRES_SCANNING` backend) ·
  [`topics/stream-ownership.md`](../topics/stream-ownership.md)
- User-facing: [`docs/formats.md`](../../docs/formats.md#directory)
