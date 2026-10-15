# Extracting

## Extracting all or some members

```python
with archivey.open_archive("download.zip") as archive:
    archive.extract_all("out/")
```

`extract_all` writes every member under the destination folder. It's safe by default: nothing lands
outside `out/`, and an archive that expands far beyond its size is stopped. [What each policy does
with unusual members](#what-each-policy-does-with-unusual-members) has the details.

To extract from a pipe, open it with `streaming=True`. Extraction reads each member once, in order,
so it needs no other option. A ZIP keeps its index at the end, so from a pipe archivey writes each
member as it arrives, then brings the files in line with the index once it gets there: it sets
permissions, turns symlinks into links, and removes what the index doesn't list. When `members` is
a function it's called twice for each member, and a `filter` is called twice for a member it left
out the first time; both see `member.member_state_final` as `False` the first time.

Its `members` argument picks what to extract. It takes names,
[`ArchiveMember`][archivey.ArchiveMember] objects from a listing, or a mix of both:

```python
with archivey.open_archive("download.zip") as archive:
    archive.extract_all("out/", members=["README.md", "docs/guide.pdf"])
```

Names must match exactly, and a directory's name ends in `/`. `members=["docs/"]` extracts only the
directory entry, not what's in it, and `members=["docs"]` extracts nothing. A name that matches
nothing is noted as the diagnostic `MEMBER_SELECTOR_UNMATCHED`, which logs a warning.

`members` also takes a function, which gets each member and returns whether to extract it:

```python
with archivey.open_archive("download.zip") as archive:
    archive.extract_all("out/", members=lambda member: member.name.endswith(".txt"))
```

`archivey.paths` builds such functions from wildcard patterns, with either of two sets of rules:

```python
from archivey.paths import match_glob, match_tar_glob

with archivey.open_archive("download.zip") as archive:
    archive.extract_all("out/", members=match_glob("docs/**"))
    archive.extract_all("out/", members=match_tar_glob("*.md"))
```

| Function | `*` | A folder's name |
|---|---|---|
| `match_glob` | Matches within one folder level, and `**` matches any number of levels, so `"docs/**"` selects a folder with everything in it | Selects only the folder, unless you pass `with_contents=True` |
| `match_tar_glob` | Matches across folders too, as in `tar`, so `"*.md"` selects every `.md` file at any depth | Selects the folder with everything in it |

Another argument, `filter`, sees each member before it's checked and written. It can change the
member by returning a changed copy, with a new name or new permissions, for example, or skip it by
returning `None`.

## Options

```python
with archivey.open_archive("download.zip") as archive:
    archive.extract_all(
        "out/",
        policy="strict",     # how much to trust names and permissions
        overwrite="error",   # what to do when a file is already there
        on_error="stop",     # whether a damaged member stops the rest
        abort_on=[],         # events that stop the whole extraction at once
        on_progress=None,    # called as files are written
        limits=archivey.ExtractionLimits(max_extracted_bytes=2 * 2**30),  # how much it may write
        dry_run=False,       # run it without keeping anything
    )
```

These are the defaults. `policy` decides how much of what the archive says about names and
permissions gets written as it is:

| `policy` | Names | Permissions |
|---|---|---|
| `"strict"` (default) | Rewritten to a portable spelling, or refused when that isn't possible | Files keep their stored permissions without setuid, setgid, sticky or group and other write, so at most `rwxr-xr-x`; a file the owner can't execute is executable by nobody; folders always `rwxr-xr-x` |
| `"standard"` | As in `"strict"`, but trailing dots and spaces are kept, and absolute paths are placed inside the destination | As stored, without setuid, setgid and sticky bits. Files are `rw-r--r--` and folders `rwxr-xr-x` when the archive stores no permissions |
| `"trusted"` | As stored, but absolute paths are placed inside the destination | As stored, and the owner too when running as root. When the archive stores no permissions, whatever your system gives a new file |

`overwrite` decides what happens when a file is already where a member would go:

| `overwrite` | Effect |
|---|---|
| `"error"` (default) | The member fails, and `on_error` decides whether extraction goes on |
| `"skip"` | The existing file stays. This isn't a failure, so `on_error` doesn't apply |
| `"replace"` | The existing file is deleted, and the member is written. An existing folder is replaced only if it's empty, and otherwise the member fails |
| `"rename"` | The member is written next to it, with a number before the extension: `a.txt` becomes `a (1).txt` |

A folder member merges into a folder that's already there. A file member whose path is an
existing folder is a conflict like any other.

`on_error` decides what a failed member does to the rest of the extraction:

| `on_error` | Effect |
|---|---|
| `"stop"` (default) | The first failure raises, and extraction stops there |
| `"continue"` | The failure is recorded in the report, and extraction goes on |

A refused member isn't a failure, so `"stop"` doesn't stop there.
`abort_on=["blocked_member"]` does.

??? note "Why doesn't a refused member stop the extraction?"
    Refusing it is the protection doing its job, and the rest of the archive is still safe to write.
    Otherwise one stray name, such as an absolute path in an archive made on another system, would
    stop everything after it.

`abort_on` lists events that stop the whole extraction immediately if they happen, even ones that
aren't failures. It's empty by default:

| `abort_on` value | Raises when |
|---|---|
| `"blocked_member"` | A member is refused |
| `"name_collision"` | Two members of the archive would be written to the same path. This raises before `overwrite` is applied, so even with `"rename"` the second member isn't written. Not checked under `"trusted"` |
| `"name_sanitized"` | A name is rewritten to its portable spelling, or an absolute name is placed inside the destination. Under `"strict"` an absolute name is refused instead, so `"blocked_member"` is the one that catches it |

After an abort there's no report, and the files already written stay on disk.

Each of these strings is also an enum value, so `policy=archivey.ExtractionPolicy.STRICT` works as
well as `policy="strict"`. The enums are `ExtractionPolicy`, `OverwritePolicy`, `OnError` and
`AbortOn`. The strings ignore case, and `-` works in place of `_`.

`on_progress` gets a snapshot after each member and every so often during a large one: the member,
the bytes and members done so far, and the expected totals when the archive records them. It's
meant for progress bars. A ZIP read from a pipe reports each member as it's written, before its
index says whether the extraction keeps it, so the count of members done can include one that's
removed at the end.

`dry_run=True` runs the same extraction into an empty temporary folder, which is deleted before the
call returns. Every member is decompressed and checked, but the files it creates stay empty, and
nothing is written to `out/`. The report shows what extracting into an empty `out/` would do, with
the path each member would be written to. Files already in `out/` aren't taken into account, so a
member that would clash with one of them still shows as extracted.

## What each policy does with unusual members

Some members are refused under every policy, and others depend on it:

| Member in the archive | `"strict"` | `"standard"` | `"trusted"` |
|---|---|---|---|
| `../evil.txt` or `a/../../evil.txt` | Refused | Refused | Refused |
| `/etc/evil.txt` or `C:/evil.txt` | Refused | Written as `etc/evil.txt` or `evil.txt` | Written as `etc/evil.txt` or `evil.txt` |
| A link to `../../outside` or `/etc/passwd` | Refused | Refused | Refused |
| A device file or a named pipe | Refused | Refused | Refused |
| `CON`, `aux.txt` or `file:ads`, which Windows can't create | Refused | Refused | Written as is |
| A name with characters that reorder how it displays, making an `.exe` look like a `.png` | Refused | Refused | Written as is |
| `invoice\u200b.pdf`, with a zero-width space, word joiner or byte-order mark | Written as is, and noted as `MEMBER_NAME_ZERO_WIDTH_CHARACTER`, which `DiagnosticPolicy.STRICT` turns into an exception | Same as `"strict"` | Same as `"strict"` |
| A name with another invisible character, such as a zero-width joiner or a soft hyphen | Written as is, and noted as `MEMBER_NAME_INVISIBLE_CHARACTER` | Same as `"strict"` | Same as `"strict"` |
| `notes. `, with a trailing dot and space | Written as `notes` | Written as is | Written as is |
| `caf\xe9.txt`, a name that isn't valid UTF-8 | Written as `caf%E9.txt` | Written as `caf%E9.txt` | Written as is |
| `what?.txt`, with a character Windows doesn't allow | Written as `what%3F.txt` | Written as `what%3F.txt` | Written as is |
| `back\slash` in a TAR | Written as `slash` in a folder `back` | Same as `"strict"` | Written as is |
| `README`, then `readme`, or `café` written two ways in Unicode | The second one counts as a file already there | Same as `"strict"` | Both written, if the disk tells them apart |
| A file with mode `rwsr-xr-x` | Written as `rwxr-xr-x` | Written as `rwxr-xr-x` | Written as is |
| A file with mode `rw-rw-rw-` | Written as `rw-r--r--` | Written as is | Written as is |

`"strict"` and `"standard"` treat `README` and `readme` as the same file on every system. An
archive that writes more in total than `limits` allows stops the whole extraction, whatever the
policy.

??? note "Why the same result on every system?"
    So code tested on one system behaves the same on another. macOS and Windows can't tell `README`
    from `readme` by default, and if `CON` were written on Linux and refused only on Windows, the
    first time you'd see the refusal is on a user's machine. `CON` and names with `:` are refused
    rather than rewritten because on Windows they're unsafe, not just unusual: `CON` opens the
    console, and `file:ads` writes data hidden inside `file`.

    A name that isn't valid UTF-8 is percent-escaped for the same reason: Linux writes it as is,
    but macOS refuses it. `%E9` is the byte's value, as in URLs, and a `%` in a rewritten name is
    written as `%25`, so the original can be worked out again. A name that already held `%E9` is
    written unchanged, so only a name archivey rewrote can be reversed: `result.presented_name` and
    `NameRewrite.PORTABLE_NAME` in `result.rewrites` say which ones those are.

A `filter` sees each member before these checks run, so it can rename one they would refuse, such as
`../evil.txt`, and that member is then written under its new name. Device files are refused whatever
the filter does, and a link pointing outside is refused unless the filter also changes its target.
`archivey.sanitize_names` is a ready-made filter for this, passed as `filter=` to `extract_all`. It
drops a leading `/` or drive letter, resolves or drops `..`, removes those reordering characters,
and adds `_` to names Windows reserves, such as `CON`. In a symlink's target it makes only the
character and reserved-name fixes, and it leaves a hard link's target as stored.

A hard link gets the contents of the file it points to in the archive, whatever happens to that
file's name. If a filter renamed that file, the link points to the new name. If the file was left
out, by `members=` or a filter, the link is written as a separate copy of its contents, except with
`streaming=True`, where that data is gone by then and the link fails. If the file is refused, the
link is refused too. Changing a hard link's target in a filter does nothing.

A refused member isn't written, and the rest of the archive still extracts. The call returns a
report with one result for each member, with the path it was written to in `result.path` and the
member as the archive stored it in `result.member`. That shows what was refused:

```python
with archivey.open_archive("download.zip") as archive:
    report = archive.extract_all("out/")
for result in report:
    if result.status is archivey.ExtractionStatus.BLOCKED:
        print(result.member.name, result.error)
```

If you'd rather stop at the first refused member, `abort_on=["blocked_member"]` raises instead.

## Damaged members

A member that fails partway leaves nothing behind. Archivey writes each file under a temporary
name and renames it only once it's complete. The files written before the failure stay on disk,
even with `on_error="stop"`. Going over the total size or entry count in `limits`, or the whole
archive expanding too much, stops the extraction even with `on_error="continue"`, since it's a sign
that the whole archive is hostile. A single member that expands too much fails like any other
member.

If the damage is in the list of members itself, such as a TAR or RAR cut short or a damaged ZIP
central directory, the members before it are written and the call then raises, with either
`on_error`, and returns no report. A damaged 7z index, or a ZIP whose end record is missing, fails
`open_archive` before anything is written.

A process killed during an extraction can leave a file named `.archivey-tmp-` followed by random
characters in the destination. It's safe to delete.

## Archives you trust

By default, an extraction stops after 2 GiB of output, after 262,144 entries, or when data
expands more than 1000 times. When the archive records both a member's size and its compressed
size, a member that would expand too much is refused before any of it is written. Other members,
and the whole archive, are checked as data is written. The expansion check ignores outputs under
5 MiB, or under 256 MiB for a whole archive read from a pipe. To extract something bigger, pass
higher `limits`, or `archivey.ExtractionLimits.UNLIMITED` to turn them off.

??? note "Why ignore small outputs?"
    Small files compress very well without being dangerous. 1 MiB of zeros compresses about 1,000
    times with DEFLATE and about 23,000 times with bzip2, so a check on every member would stop
    ordinary archives. A real bomb writes far more than 5 MiB, so it's still caught.

    From a pipe, archivey doesn't know the archive's size, so it compares what it has written with
    what it has read so far. An honest archive that starts with a large blank file looks like a
    bomb until the rest arrives, so that check waits longer.

A sparse file in a TAR, such as a disk image, is stored without its empty regions, so it can expand
far more than 1000 times and stop the extraction. `member.is_sparse` tells you which members are
sparse.

Some limits apply before extraction starts, such as the number of members or how much memory a
decoder may use. Those are set when you open the archive, through
`config=archivey.ArchiveyConfig(...)`, and the [reference][archivey.ArchiveyConfig] lists
them.

[Security](security.md) covers what archivey relies on and where its guarantees stop.
