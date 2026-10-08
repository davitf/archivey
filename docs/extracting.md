# Safe extraction

Archivey extracts **safely by default**. You opt *out* of protections; you do not opt in.

## Extracting everything

```python
with archivey.open_archive("archive.zip") as reader:
    reader.extract_all("out/")
# policy=ExtractionPolicy.STRICT, overwrite=ERROR, on_error=STOP
```

To extract a TAR or a single-file compressed stream from a pipe or a socket, pass
`streaming=True` to `open_archive`: extraction is a single forward pass, so it needs no
random access. ZIP, ISO, 7z and RAR keep their index away from the front of the file, so
they cannot be read from a pipe in either mode; save them to a file or a `BytesIO` first
([Non-seekable sources](access-and-cost.md#non-seekable-sources)).

## Trust boundaries

- **The archive is untrusted.** Every byte of it: member names, link targets, sizes,
  timestamps, comments, header structures, compressed streams. Crafted and adversarial
  archives are in scope for *all* guarantees, not just well-formed ones.
- **The destination directory and local filesystem are trusted at rest** — but not
  their *contents produced by the extraction itself*: an earlier extracted member is
  untrusted input to the handling of every later member (this is why symlink targets
  are re-resolved against the live tree after creation).
- **The local process and other local processes are trusted.** Concurrent hostile
  modification of the destination *by another process* during extraction (a local
  attacker racing us) is out of scope; if that ever changes, `O_NOFOLLOW`/`openat`-style
  extraction is the direction.
- **A directory opened as a source is the exception.** Another process may change a tree
  while archivey reads it. On POSIX the walk opens each subdirectory without following a
  symlink and checks it is the directory it listed; if not, the listing fails with
  `OSError` (Windows is weaker, see below). Reading a member follows no symlink and
  checks that the file is still the one listed, at the listed size; if not, the read
  fails with `OSError`. A file rewritten in place at the same size reads its new
  content. On a filesystem that reports no file identity (some FUSE and network mounts),
  only the file type and size are checked.
- **Optional dependencies and external tools** (`pycdlib`, codec packages, the `unrar`
  and `unar` programs) are trusted code but *not* trusted to be robust: their failures
  surface as translated archivey errors, except in the accepted cases below.

### Known and accepted limits

These are the places where the guarantees above stop. Each one is a trade-off that was
chosen, not a bug waiting for a fix, so please don't report them as vulnerabilities.

- **A native decoder that crashes can take the process with it.** Decoders written in
  C run inside your process: the standard library's `zlib`, `bz2` and `lzma`, pyppmd
  for PPMd members up to `DecoderLimits.max_ppmd_in_process_input` (16 MiB by default),
  and the optional bzip2 accelerator. The known crashes have been designed around: the
  rapidgzip accelerator for gzip, zlib and raw DEFLATE runs in a child process, a larger
  PPMd member is decoded in a child process, and a smaller one is handed to pyppmd whole,
  which avoids the input pattern that crashes it. A crash nobody has found yet in an
  in-process decoder would still abort the process. The bzip2 accelerator stays
  in-process because no crash has been seen in it.
- **`MemoryError` is not translated.** It passes through as itself, so that running out
  of memory is never mistaken for a damaged archive. The `DecoderLimits` and
  `ListingLimits` caps are there to keep a hostile archive from getting that far.
- **Nothing bounds CPU or wall-clock time.** The limits cap bytes, entries and key
  derivation work, not time. A slow decode of a legitimate-looking member is not an
  error. Enforce a timeout outside archivey if you need one (a worker process you can
  kill is the reliable way).
- **Accelerators are on by default when installed.** `AcceleratorMode.AUTO` uses them
  when the `[seekable]` extra is present and a caller asks for seeking. They sit outside
  the fuzzed surface (see the hardening notes below); set them to `OFF` for untrusted
  input under a strict threat model.
- **After a seek, a crafted `.xz` or `.lz` index can serve the wrong bytes with no
  error.** The integrity guarantee covers a read from start to end with no seek
  ([Errors and diagnostics](errors-and-diagnostics.md#the-integrity-guarantee)).
- **On Windows, a directory source is less protected against concurrent changes.**
  There is no `O_NOFOLLOW`, so the walk scans subdirectories by path: a subdirectory
  swapped for a junction or symlink while the walk runs can list entries from outside
  the root. Reads still check the file identity where the filesystem reports one.

## What is enforced

- **Path traversal:** `..` components (any separator) and null bytes are rejected
  before any write; the destination parent is resolved and containment-checked
  (`safe-extraction`, `internal/filters.py`). An absolute name (a leading `/`, a drive
  letter or a UNC prefix) is refused under `STRICT`. `STANDARD` and `TRUSTED` drop the
  root and extract it inside the destination (`/etc/x` → `etc/x`, `C:/x` → `x`), as
  GNU tar, bsdtar, unzip and 7-Zip do, and record the stored name in
  `ExtractionResult.presented_name`. A drive letter with no separator after it (`a:b`)
  is refused at every policy: it
  is also an ordinary POSIX name, so there is no root to drop. Your
  `filter` runs before these checks, so it sees every member and can rename an unsafe
  one; the name it returns is the one checked. `archivey.sanitize_names` is a ready-made
  filter that renames instead of refusing: it drops roots, resolves or drops `..`,
  removes bidi overrides, and adds `_` to Windows-reserved names and `:`. It rewrites a
  symlink target's characters and segments the same way (`file:stream` →
  `file_stream`), but keeps its root and its `..`, and leaves a drive or UNC target to
  be refused. A target read only after your filter ran (see "Symlink targets stored as
  member data" below) is rewritten too, because `extract_all` calls the filter again.
- **Extraction-root overwrite:** a *file* member whose normalized name is `"."` or `""`
  is rejected (`FilterRejectionError`); only a directory member may name the extraction
  root. Prevents a corrupt archive from replacing the destination directory with a
  regular file (`internal/filters.py` `check_universal`).
- **Symlink escapes, three layers:** lexical target check at planning time; parent-dir
  resolution; and post-`os.symlink` re-resolution against the real filesystem (catches
  chained-symlink attacks staged by earlier members). Escaping links are removed and
  rejected. A later member can change where an earlier link points: with `l -> a/../x`
  extracted first, a later `a -> .` makes `l` point outside. So a link is rechecked
  when a later member changes a path the link goes through, before the next member is
  extracted. A link that now escapes is removed, and its result, which a progress
  callback may already have seen as `EXTRACTED`, becomes `BLOCKED`. A `..` in a target
  that stays inside the destination is not refused.
- **Windows symlink targets:** a symlink target with a drive letter (`C:/Windows`, `C:x`)
  or a UNC root (`//server/share`) is refused at every policy and on every OS. Windows
  would follow it out of the destination, and refusing it everywhere means an archive
  extracts the same way wherever you extract it. One exception: a symlink target that
  starts with a single `\` (`\foo`) extracts on POSIX, where a backslash is an ordinary
  filename character; Windows refuses it. A Windows symlink or junction from a ZIP, 7z or
  RAR archive lists with `/` separators and without the `\??\` prefix (`\??\C:\Windows`
  lists as `C:/Windows`, `..\up\x` as `../up/x`). Under `STRICT` and `STANDARD`, a `:` or
  a Windows-reserved device name in a target segment (`file:stream`, `sub/NUL`) is
  refused, as it is in a member name; `TRUSTED` leaves those to the OS.
- **Hardlink targets** name an earlier member, and the link gets what that member
  gets. The target is looked up as a member name (the latest earlier member of that
  name, so a crafted duplicate cannot redirect a link) and is never used as a path:
  the link is made to the file that member was written to. So the target string is not
  checked, and a link is refused (`BLOCKED`, "Hardlink target was refused") when the
  member it names is refused, as `../x` is at every policy and `/x`, `\x` or `C:/x`
  are under `STRICT`. Under `STANDARD` and `TRUSTED` those last three members are
  re-rooted and written, and their links extract. A target that names no earlier
  member fails with `LinkTargetNotFoundError`. A `filter` that changes a hardlink's
  `link_target` changes nothing, `sanitize_names` included: the link still follows the
  stored name. A filter that renames the member it names does matter, both ways.
- **Never write through a symlink:** overwrite handling replaces symlinks, never
  follows them; atomic temp-file + `os.replace` writes mean interrupted extraction
  never leaves a half-written destination file. The destination root itself is yours,
  so if it is a symlink to a directory, archivey follows it and extracts into the
  target (as `tar -C` and `unzip -d` do).
- **Special files** (devices, FIFOs, sockets) are always rejected; an NTFS junction is
  never traversed, because it is a link and extraction never follows one. It is
  *flagged* as a junction — `extra["is_junction"]` — only where the archive says so,
  which in practice means RAR and a directory tree read from a Windows filesystem. ZIP
  and 7z carry the flag too when the writer stored the junction's reparse data, but
  7-Zip does not store it for a directory, and a junction is always a directory, so in
  practice a junction from those two arrives as a link with no target rather than a
  flagged one. `extra["is_reparse_point"]` is the weaker fact those archives *do*
  record — this was a Windows symlink or junction rather than a POSIX one — and is set
  from metadata in every format that states it.
- **A link for which the archive records no target** (a stored target that is the empty
  string counts, and lists as `link_target=None`) is recorded
  `ExtractionStatus.LINK_TARGET_UNAVAILABLE` and the rest of the archive still extracts.
  Nothing can be written for it, and nothing about the extraction went wrong, so it is
  not a failure and `OnError.STOP` does not abort on it. That holds in a streaming read
  too: extraction reads a link's data before writing it, so an omission legible only in
  that data is seen in time. The omission is the archive's, and it is reported as
  `SYMLINK_TARGET_UNAVAILABLE` on the diagnostics channel — an archive-integrity code,
  so `DiagnosticPolicy.strict()` still refuses such an archive outright. A link whose
  target the archive *does* carry but this read could not reach — encrypted,
  compressed, split across volumes with a part missing, or damaged — is a per-member
  failure instead, because recording it as an outcome would drop a member the archive
  describes in full while reporting success. In ZIP, 7z and RAR3/4, a damaged target
  (its data fails the CRC or HMAC, or the decompressor) does not fail the listing: the
  link is listed without a target, reported with `reason="target_data_damaged"`, and
  opening or extracting it raises the damage.
- **A link target longer than 4096 bytes** is treated as corrupt or malicious when it is
  stored as the member's data (ZIP, 7z, RAR4). No filesystem path that long exists on
  Linux or macOS, and the data can be compressed, so reading it whole would let a small
  archive allocate gigabytes while you only listed it. The target is left unset — never
  truncated, which would point the link somewhere the archive did not say — and reported
  as `SYMLINK_TARGET_UNAVAILABLE` with `reason="target_too_long"`. Like the unreachable
  targets above, the link is a per-member failure, and `DiagnosticPolicy.strict()`
  refuses the archive.
- **Deceptive names:** a member name (or link target) containing a Unicode bidi
  **override or isolate** — U+202A–202E, U+2066–2069 — is rejected with
  `FilterRejectionError` under `STRICT` (the default) and `STANDARD`. Those characters
  reorder the surrounding text, which is how `evil‮gnp.exe` displays as `evil.png` in
  every listing a person will see. The three *directional marks* (U+061C, U+200E,
  U+200F) are **not** rejected: they reorder nothing and occur in legitimate Arabic and
  Hebrew filenames. Right-to-left script itself is unaffected — `فهرس.txt` contains no
  control character at all. Listing and reading always present either kind exactly as
  stored, in a name or a link target, with one `MEMBER_NAME_BIDI_CONTROL` diagnostic
  for each; its context's `field` is `"name"` or `"link_target"`. A target stored as the
  member's data (ZIP, 7z, RAR4) is reported when it is read.

    Unlike the rules above, this one **is** lifted by `TRUSTED`, which extracts the
    member under its stored name. The distinction is that nothing here is unsafe to
    *write* — the file lands inside your destination under exactly its stored bytes; what
    is misleading is the name you read back later. `TRUSTED` means "faithful bytes", and
    faithful round-tripping (mirroring an archive, converting between formats) needs a
    route that the default correctly refuses. A caller filter that renames the member
    also works at any policy, since the check runs on the final name.
- **Decompression bombs at extraction:** cumulative output cap, per-member ratio,
  archive-wide static ratio, **live** ratio for unknown-size/pipe sources, and an entry
  count cap — the global guards halt even under `OnError.CONTINUE`.
- **Permission hygiene:** setuid/setgid/sticky stripped except under `TRUSTED`;
  ownership applied only under `TRUSTED` as root.
- **Cross-platform name safety (STRICT/STANDARD):** casefold+NFC collision tracking,
  reserved device names and `:` rejected, trailing-dot/space strip, non-UTF-8
  percent-escape sanitization, `OverwritePolicy.RENAME` (ADR 0013 / PRs #109/#123).
  Directories are not in the collision map, and archivey checks a directory member
  against it only when a file or symlink already holds its destination. So a *file* `x`
  collides with a *directory* `x/` stored after it, but not with one stored before it.
  A *file* `Foo` and a later *directory* `foo/` that differ only by case collide only
  on a case-insensitive filesystem.
- **Error honesty:** codec/library exceptions are translated to typed `ArchiveyError`s
  with context; genuine I/O errors propagate unchanged; no handler swallows or
  reclassifies an unknown exception.
- **Accelerator lifecycle:** C++-threaded accelerators are close-guarded
  (`weakref.finalize`) so crafted-input error paths cannot leave aborting threads
  (see [the rapidgzip report](https://github.com/davitf/archivey/blob/main/dev-docs/investigations/rapidgzip-upstream-report.md), §6).

Atomic file writes stage into temp siblings named `.archivey-tmp-<random>` inside the
destination directory. Any Python-level failure removes them; only a hard kill
(SIGKILL, power loss) can leave one behind. Leftover `.archivey-tmp-*` files in an
extraction destination are archivey's staging files and are safe to delete before
re-running the extraction.

## Policies

```python
from archivey import ExtractionPolicy, OverwritePolicy, OnError, ExtractionLimits, ListingLimits

with archivey.open_archive("archive.zip") as reader:
    reader.extract_all(
        "out/",
        policy=ExtractionPolicy.STRICT,       # default
        overwrite=OverwritePolicy.ERROR,      # or REPLACE / SKIP
        on_error=OnError.STOP,                # or CONTINUE — failures only
        limits=ExtractionLimits(...),         # or ExtractionLimits.UNLIMITED
    )

with archivey.open_archive(
    "huge.zip",
    config=archivey.ArchiveyConfig(listing_limits=ListingLimits(max_members=10_000)),
) as reader:
    reader.members()  # ResourceLimitError if the central directory is larger

```

Every enum argument here also takes the member spelled as a string, so a quick script
or a shell one-liner does not need the imports: `overwrite="skip"`, `on_error="continue"`,
`abort_on=["blocked-member"]`, `format="tar.gz"`. Case is ignored and `-` and `_` are
interchangeable, which is the spelling the CLI's own `--help` advertises. The CLI takes
the same set — `--abort-on blocked_member` and `--abort-on blocked-member` are the same
option — so a value copied either way round works in both places. A spelling that
matches nothing raises `ArchiveyUsageError` at the call, naming the ones that would have
worked — it is never quietly ignored.

`OnError` governs per-member **failures** (corrupt/truncated data, write errors,
overwrite conflicts under `ERROR`). A policy **block** — an unsafe member refused by a
universal path-safety check or a policy filter — is always recorded as `BLOCKED` and
extraction continues, under either `STOP` or `CONTINUE`.

An archive whose member list ends in damage (a TAR or RAR that is cut or corrupt
partway, for example) does not fail closed. A TAR has no index, so its member list is
read during its one forward pass, in either access mode; a RAR lists header by header.
The members listed before the damage are written, and then the call raises the damage,
usually `TruncatedError` or `CorruptionError`, under either `OnError`. No report is
returned. This is what unrar and 7-Zip do, and the order `stream_members()` gives. A hard
link in that prefix still gets its content, because its source always comes before it. A
7z or ZIP keeps its member list in one index, so damage there fails `open_archive()` and
nothing is written.

To abort the whole archive on the first unsafe member (fail-closed strict security),
pass `abort_on`:

```python
from archivey import AbortOn

with archivey.open_archive("untrusted.zip") as reader:
    reader.extract_all("out/", abort_on={AbortOn.BLOCKED_MEMBER})
```

`abort_on` is independent of `OnError` and names three events:

| Member | Fires when | Raises |
| --- | --- | --- |
| `BLOCKED_MEMBER` | a member is refused by a path-safety check or a policy filter | the underlying `FilterRejectionError` |
| `NAME_COLLISION` | a second member resolves to an already-written destination (non-`TRUSTED`) | `NameCollisionError` |
| `NAME_SANITIZED` | a name is rewritten to its portable spelling, or an absolute name is re-rooted | `NameRewrittenError` |

An abort is immediate: no later member is processed and **no report is returned** — so
handle the exception, not a return value. Output already written for earlier members
stays on disk, exactly as with `OnError.STOP`; an abort stops the run, it does not roll
it back.

`NAME_COLLISION` fires on every collision whatever `OverwritePolicy` does with it —
replaced, skipped, errored or renamed — because the trigger is the collision, not its
resolution.

`NAME_SANITIZED` is a **narrow escape hatch**, not part of ordinary strict extraction:
it fires on a *successful* safety rewrite, and no policy or preset implies it. Set it
only if any on-disk name differing from the archive's is unacceptable to you — a
mirroring tool, a forensic extract, a byte-fidelity check. To merely *audit* rewrites,
read `ExtractionResult.presented_name` and let extraction finish.

| Policy | Intent |
| --- | --- |
| `STRICT` | Untrusted archives (default) |
| `STANDARD` | Archives you trust more, such as your own older ones. Keeps the stored permission bits, execute included, but strips setuid, setgid and sticky and never applies ownership. Keeps trailing dots and spaces in names; the other name rules are the same as under `STRICT` |
| `TRUSTED` | Allow ownership / sticky bits when running as root; still no traversal |

Selective extract:

```python
with archivey.open_archive("a.zip") as reader:
    reader.extract_all("out/", members=["only/this.txt"])
```

## Dry run

Pass `dry_run=True` to `extract_all()` to see what an extraction would do
without keeping anything:

```python
from archivey import ExtractionStatus

with archivey.open_archive("backup.tar.gz") as reader:
    report = reader.extract_all("out/", on_error="continue", dry_run=True)
for result in report.results:
    if result.status is not ExtractionStatus.EXTRACTED:
        print(result.status.value, result.member.name, result.error)
```

A dry run is the real extraction, run into a private scratch directory under the system
temp directory, which is removed before the call returns. Directories and links are
created there, so checks that depend on what is already on disk, such as a symlink an
earlier member created, give the same answer they would in `out/`. Every file body is
read, decompressed and checked against its stored digest and the limits, then thrown
away: the files are created empty. A dry run therefore takes as long as an extraction,
but needs one inode per member instead of the space for the content.

The report reads as if `out/` had been empty. Files already in `out/` are not read, so
they cause no collisions, and `out/` is neither created nor changed. A symlink whose
absolute target names a path inside `out/` is checked against that path, as in a real
extraction.

A few answers can differ from a real extraction, because the scratch directory is not
`out/`:

- If a link target leaves `out/` and comes back in through a symlink outside it, or
  climbs above the directory that holds `out/`, the dry run blocks the link. A real
  extraction could keep it.
- Everything is on one filesystem. If `out/` spans a mount point, a real extraction
  copies a hardlink that crosses it and counts the copy against `max_extracted_bytes`.
  The dry run does not count those bytes.
- If `out/` does not exist, the dry run predicts whether it could be created by asking
  the system whether the nearest directory that exists can be written to. The answer
  is for your real user and group ids, so a program running setuid can get a
  different answer from the real extraction. On Windows nothing is checked, so a
  permission problem there shows only in a real extraction.

## Names change on disk

Archive order and identity matter more than “the” name.

- `get(name)` is **last-wins** when names collide.
- `extract_all(members=["x"])` matches **every** member named `x`; pass an
  `ArchiveMember` when you mean one identity.
- Hardlink targets resolve to an **earlier** same-named member by `member_id`, not
  to “whichever `get` would return.”
- Members with `is_current=False` (for example RAR version history) stay visible in
  listings but are skipped on extract by default.

| Need to know | Detail |
| --- | --- |
| Safe ≠ unlimited | Traversal, symlink escapes, and bombs are blocked; huge/hostile archives can still raise `ResourceLimitError` unless you raise limits. |
| STRICT and STANDARD rewrite some names | Both percent-encode bytes that are not valid UTF-8; `STRICT` also strips trailing dots and spaces. Only `TRUSTED` writes names as stored. Disk path may differ from `member.name` — read `ExtractionResult.presented_name` for the pre-rewrite spelling. |
| Collisions are first-class | Under `STRICT`/`STANDARD`, `README`/`readme` (and NFC/NFD twins) collide on **all** platforms. `OverwritePolicy` applies; `REPLACE` is not a silent merge — the clobbered member's result is revised to `OVERWRITTEN`. Use `OverwritePolicy.RENAME` (`photo (1).jpg`) for intentional duplicates. |
| Collision vs pre-existing file | `ExtractionResult.collided_with` names the already-written path a member collided with, under every resolution (skip, error, replace, rename), for a directory member landing on a file as for a file. It is `None` when the destination was simply already on disk — otherwise the two are indistinguishable. |
| `RENAME` and directories | When a file or symlink, yours or the run's, holds a directory member's name, archivey writes the directory as `name (1)/` and keeps the file. The members inside it follow it: `dd/f` lands at `dd (1)/f`, and its result reports `requested_path` `dd/f` and `path` `dd (1)/f`. The CLI reports the directory's rename once, not once per member. |
| `REPLACE` and directories | `REPLACE` removes an existing directory only when it is empty. A non-empty one fails that member with `ExtractionError`, so a later member cannot delete files the run already wrote or files you already had. When the run wrote the empty directory it removes, that directory's result is revised to `OVERWRITTEN`, like any clobbered member. Its `collided_with` stays `None` and `AbortOn.NAME_COLLISION` does not fire, because directories are not in the collision map. |
| Directories you already had | A directory member over a directory that was there before the run, including the destination itself (a `./` entry), leaves its mode and times alone. When the archive asked for a different mode, the result's `kept_mode` holds the mode the directory kept. |
| Reserved names / `:` | Rejected under `STRICT`/`STANDARD` on every platform (`CON`, `NUL`, `file:ads`, …). |
| `OnError.CONTINUE` ≠ ignore bombs | Per-member failures can continue; global bomb and listing guards still stop. |
| `OnError.STOP` is failures-only | Policy blocks are always recorded and continued; inspect the report (or exit `3` on the CLI) for `BLOCKED`. To raise instead, pass `abort_on={AbortOn.BLOCKED_MEMBER}`. |
| `TRUSTED` still won’t traverse | Ownership / sticky bits only when allowed; path safety stays on. |
| Hardlinks + filters | Excluding a hardlink’s source can orphan the link (especially on streaming sources); `OnError` decides fail vs continue. A source the policy would refuse is not recovered: the link is `BLOCKED`. Rewriting a hardlink’s `link_target` in a filter has no effect. |
| Symlink-hostile filesystems | Unlike `tarfile`, archivey does **not** copy target bytes through a symlink; you get a typed failure or skip. |
| Staging leftovers | `.archivey-tmp-*` under the destination, and `archivey-dry-run-*` directories in the system temp directory, are safe to delete (left only after hard kill / power loss). |
| Nested archives | Recursion is caller-driven; a zip-quine loops only if you loop. Bound depth/size yourself. |
| Listing vs extract limits | Bomb guards apply during **extraction**. `ListingLimits` apply when materializing `members()`. `stream_members()` / `streaming=True` are intentionally unguarded, except on formats that already apply `max_members` at parse (7z, RAR and ISO): `open_archive` itself raises. ISO also weighs the directory records `pycdlib` parses at open against `max_metadata_bytes`. RAR also weighs its compressed RAR 1.5/2.x comments against `max_metadata_bytes` at open, and TAR refuses a single PAX or GNU long-name header larger than the whole `max_metadata_bytes` in every mode. Encrypted 7z password confirmation runs on the first member read, before extract limits: peak RAM is one 64 KiB chunk plus codec buffers, and wall time scales with folder size × candidates only for store/copy+AES whose only CRC is at the folder end. |

## Limits

Defaults (via `ExtractionLimits` / `ListingLimits` / `DecoderLimits` / `SpoolLimits` on
`ArchiveyConfig`) cap:

- **Extraction bombs** — total extracted bytes (default 2 GiB), compression ratio
  (default 1000, checked once 5 MiB has been written), and entry count (default
  1,048,576) (`ExtractionLimits`). Trips raise `ResourceLimitError`.
- **Listing materialization** — member count (default 1,048,576) and retained metadata
  bytes (default 64 MiB) (`ListingLimits`) on `members()` / `scan_members()` /
  extract-prep materialization. Trips raise `ResourceLimitError`. A TAR extraction
  does not list first: it checks the limits as each member arrives in its one pass, so
  members before the one that crosses a cap are already written when it raises.
  `stream_members()` / `streaming=True`
  stay unguarded by design, except on 7z, RAR and ISO where `max_members` is checked
  at `open_archive`. Raise `listing_limits.max_members` to open a larger 7z, RAR or
  ISO. For 7z and RAR that parse bound is a member count, not a byte budget:
  `max_metadata_bytes` still fires when the list is materialized. RAR also checks it at
  `open_archive` against the declared sizes of compressed RAR 1.5/2.x comments, before
  decoding them. ISO checks it at `open_archive` against the bytes of the directory
  records `pycdlib` parses, which are more than the text a listing keeps; and ISO
  counts every directory record of one tree, so an image right at a cap may need a
  slightly higher one.
- **Decoder memory** — the working set a codec allocates because the *archive's* header
  said to, such as a 7z PPMd window or an LZMA dictionary (`DecoderLimits`, default
  2 GiB). Checked before the allocation, on `open()` / `read()` as much as on
  `extract_all()`, so it is neither a listing nor an extraction cap. Trips raise
  `ResourceLimitError`. Format detection is the exception: a `.lzma` or compressed-tar
  sample is decoded uncapped to recognise it, so under a memory cap an oversized
  declaration can surface as `MemoryError` from `open_archive` instead.
  RAR is covered too, although `unrar` or `unar` decodes its data in a separate process.
  The dictionary size a RAR header declares is checked before that process starts. The
  size is counted as the program allocates it. `unar` uses the whole declared
  dictionary, so the declared size counts. `unrar` uses no more of it than the data it
  decodes, so the count is capped at the member's unpacked size. In a solid archive the
  cap is the unpacked size of the members up to and including the one read. An earlier
  member that `unrar`'s include mask also selects counts too, because `unrar` decodes it
  first: a duplicate name, or a glob under `rar_allow_glob_member_concatenation`.
- **Key-derivation work** — RAR5 and 7z headers say how many hashing rounds turn a
  password into a key, and an archive can salt every member so each needs its own
  (`DecoderLimits.max_key_derivation_rounds`, default `2**27` rounds in total per open
  archive: about half a minute of hashing when spent on RAR5 derivations at their
  2^24-round maximum, closer to a minute for RAR3 and about a quarter of one for 7z).
  Keys the reader already derived are reused for free, so an ordinary encrypted
  archive spends one or two derivations; each wrong candidate password counts. Trips
  raise `ResourceLimitError` before the derivation starts.
- **Temporary copies of a stream source** — RAR member data goes through an external
  program (`unrar` or `unar`), which reads only files, so a RAR opened from a stream is
  copied to a temp file first (`SpoolLimits.max_bytes` on
  `ArchiveyConfig.spool_limits`, default 1 GiB across the whole copy). Checked before
  anything is written. Trips raise `ResourceLimitError`.
  A path source is read in place, with two exceptions bounded by the same limit: a
  RAR with a prefix before it (an SFX stub) read with `rar_decompressor="unar"`, and
  a list of RAR volume files where the system allows no link to them.
- **PPMd members decoded in-process** — pyppmd, the PPMd decoder (7z and ZIP method
  98), can crash the whole process on corrupt input unless it is handed a member in one
  piece. archivey holds a member's compressed bytes up to
  `DecoderLimits.max_ppmd_in_process_input` (default 16 MiB) and decodes larger ones
  in a child Python process, where a crash (a fault signal such as SIGSEGV) becomes
  `CorruptionError`. Output streams either way. A child killed by SIGKILL (usually the
  out-of-memory killer), or one that dies allocating the member's model under a memory
  cap, raises `ResourceLimitError`. So does a larger member where no child process can
  be started (a frozen application, an interpreter that does not know its own path, a
  spawn the operating system refuses, or a child that cannot import pyppmd); `None`
  decodes every member in-process, holding its whole compressed size in memory. A
  child ended any other way (SIGTERM, a plain exit status) raises `ReadError`, which is
  not a verdict on the data.

Loosen per call with `limits=` (extraction only), raise `listing_limits`,
`decoder_limits` or `spool_limits` at `open_archive(config=…)`, or use
`ExtractionLimits.UNLIMITED` / `ListingLimits.UNLIMITED` / `DecoderLimits.UNLIMITED` /
`SpoolLimits.UNLIMITED` for trusted inputs you control.
An open reader keeps the config it was opened with: `extract_all()` takes no `config=`,
and its `limits=` covers extraction limits only. To raise a listing or decoder ceiling,
open the archive again with a new `ArchiveyConfig`.

Bomb guards apply during **extraction**. Listing caps apply when a full member list is
materialized — prefer `stream_members()` for huge untrusted archives when you only need
a sequential subset, except on 7z, RAR and ISO where `max_members` is already checked at
open (`stream_members()` / `streaming=True` included). Encrypted 7z folders
confirm the password by decoding on the first
member read, which is neither listing nor extract: peak memory is a 64 KiB chunk plus
codec buffers. The decode stops at the first member CRC covering 4 bytes, or at 64 KiB
of output for a compressed folder, whose codec rejects a wrong key within a few bytes.
It reaches folder size per candidate only for **store/copy+AES** whose only CRC is at
the folder end, where nothing rejects a wrong key early.

**Symlink targets stored as member data.** ZIP, 7z and RAR4 keep a symlink's target in
the member's data rather than its header, so learning where a link points means reading
that data, which can mean decompressing it and asking your password provider. By default
(`ArchiveyConfig.read_link_targets=True`) listing reads every such target, and a
`stream_members()` pass reads them all by the time it finishes, whether you selected the
links or not; on 7z that decodes each link's folder up to its last link, once. For an
untrusted archive you only mean to list, `read_link_targets=False` stops the reader
reading any of them on its own: those links list with `link_target=None` and no
diagnostic. Extraction still writes them. When extraction reaches a link whose target is
still unread, under `read_link_targets=False` or in a streaming pass, `extract_all` runs
your `members` selector and `filter` on the link first, with `link_target=None`, and
reads the target only for a link both accept; it then calls your `filter` again with the
target, so a filter that rewrites targets, such as `sanitize_names`, sees it, and a
filter can see such a link twice. A target it cannot read fails that member under
`on_error`. `open()` on a link reads its target to follow it. Either way the target is
filled in place on the member you hold. Like `listing_limits`, the setting is fixed for
the reader's lifetime.

That read can show the member is not a link at all. A member flagged as a Windows
reparse point whose data is not a reparse buffer is a file, and listing would have
presented it as one. When extraction is the first to read it, under
`read_link_targets=False` or in a streaming pass, `extract_all` re-types it and calls
your `filter` a second time, now with the file, so a filter can see such a member twice.
In random access it then writes the file's content. A streaming pass has already gone
past that content, so the member fails under `on_error` instead.

A hard link to a symlink, directly or through other hard links, goes the other way: your
`filter` sees the HARDLINK the archive lists, and its result keeps that member, but what
is written is a second symlink with the same target, as GNU tar makes it. It is checked
like any other symlink.

**The bomb tracker is per-archive, not nesting-aware.** It measures the expansion of
the archive it is extracting, so a zip-of-zips can amplify past your budget one level
at a time. Recursion into nested archives is caller-driven: if you open extracted
members as archives, bound the depth and the cumulative size yourself.

## Hardening notes for callers

**Optional `[seekable]` accelerators** (`rapidgzip` and its bundled bzip2
decoder) are a performance path, not part of the defended fuzz surface. The default is
`AcceleratorMode.AUTO`, which engages them when the `[seekable]` extra is installed and
a caller asks for seeking, so turning them off is something you do yourself. The
gzip, zlib and raw DEFLATE decoder runs in a child process, so a native abort there
costs only the member; a busy loop in that child is not bounded by a timeout. The bzip2
decoder runs in-process. Third-party C++ can busy-loop on crafted input in a way Python
timeouts cannot cleanly interrupt. Callers processing untrusted archives under a hard
latency budget should turn accelerators off (`use_rapidgzip` and `use_indexed_bzip2`
set to `AcceleratorMode.OFF`) or enforce their own resource limits. Mutation and
Atheris harnesses run with accelerators off for this reason.

**External tools:** RAR member *data* is decompressed by an external program: RARLAB
`unrar` or `rar`, or `unar` under the default `rar_decompressor="auto"` when no RARLAB
program is installed. Each is found on the process `PATH`. `unar` receives a password on
its command line, where other local users can see it while it runs; select
`RarDecompressor.UNRAR` to rule that out. If you don't trust either program, select
`RarDecompressor.NONE`: archivey then runs neither, and refuses every RAR member it
cannot read without them (anything compressed, encrypted or solid);
`extract_all(..., on_error="continue")` then writes the members it can read and records
the rest. Keep these tools updated; treat their availability and behaviour as part of
your deployment’s trust boundary.

Prefer extracting untrusted archives into a dedicated directory with limited
permissions, then validating results before promoting them elsewhere.

## Diagnostics

Every block and every name rewrite is recorded on the returned `ExtractionReport`, not
only in logs — see [Errors and diagnostics](errors-and-diagnostics.md).
