# Extracting

## Extracting all or some members

```python
archivey.extract("download.zip", "out/")
```

`extract` opens the archive, writes every member under the destination folder, and closes it
again. It's safe by default: nothing lands outside `out/`, and an archive that expands far beyond
its size is stopped. [What each policy does with unusual members](#what-each-policy-does-with-unusual-members) has the details.

On an open archive, `extract_all` does the same, and its `members` argument picks what to
extract. It takes names, [`ArchiveMember`](api.md#archivey.ArchiveMember) objects from a listing,
or a mix of both:

```python
with archivey.open_archive("download.zip") as archive:
    archive.extract_all("out/", members=["README.md", "docs/guide.pdf"])
```

`members` also takes a function, which gets each member and returns whether to extract it:

```python
with archivey.open_archive("download.zip") as archive:
    archive.extract_all("out/", members=lambda member: member.name.endswith(".txt"))
```

A second argument, `filter`, sees each member just before it's written. It can change the member
by returning a changed copy, with a new name or new permissions, for example, or skip it by
returning `None`.

## Options

```python
archivey.extract(
    "download.zip", "out/",
    policy="strict",     # how much to trust names and permissions
    overwrite="error",   # what to do when a file is already there
    on_error="stop",     # whether a damaged member stops the rest
    abort_on=[],         # events that stop the whole extraction at once
    limits=archivey.ExtractionLimits(max_extracted_bytes=2 * 2**30),  # how much it may write
)
```

These are the defaults, and `extract_all` takes the same arguments. `policy` decides how much of
what the archive says about names and permissions gets written as it is:

| `policy` | Names | Permissions |
|---|---|---|
| `"strict"` (default) | Rewritten to a portable spelling, or refused when that isn't possible | Files at most `rw-r--r--` and never executable, folders at most `rwxr-xr-x` |
| `"standard"` | As in `"strict"`, but trailing dots and spaces are kept | As stored, without setuid, setgid and sticky bits |
| `"trusted"` | As stored | As stored, and the owner too when running as root |

`overwrite` decides what happens when a file is already where a member would go:

| `overwrite` | Effect |
|---|---|
| `"error"` (default) | The member fails, and `on_error` decides whether extraction goes on |
| `"skip"` | The existing file stays. This isn't a failure, so `on_error` doesn't apply |
| `"replace"` | The existing file is deleted, and the member is written |
| `"rename"` | The member is written next to it, as `name (1)` |

A folder that's already there is never a conflict. Members are written into it.

`on_error` decides what a failed member does to the rest of the extraction:

| `on_error` | Effect |
|---|---|
| `"stop"` (default) | The first failure raises, and extraction stops there |
| `"continue"` | The failure is recorded in the report, and extraction goes on |

`abort_on` lists events that stop the whole extraction immediately if they happen, even ones that
aren't failures. It's empty by default:

| `abort_on` value | Raises when |
|---|---|
| `"blocked_member"` | A member is refused |
| `"name_collision"` | Two members of the archive would be written to the same path. This raises before `overwrite` is applied, so even with `"rename"` the second member isn't written. Not checked under `"trusted"` |
| `"name_sanitized"` | A name is rewritten to its portable spelling |

After an abort there's no report, and the files already written stay on disk.

Each of these strings is also an enum value, so `policy=archivey.ExtractionPolicy.STRICT` works as
well as `policy="strict"`. The enums are `ExtractionPolicy`, `OverwritePolicy`, `OnError` and
`AbortOn`. The strings ignore case, and `-` works in place of `_`.

## What each policy does with unusual members

<!-- Revisit after PR 524 lands: it changes how standard/trusted handle absolute names and when
the filter runs. -->

Some members are refused under every policy, and others depend on it:

| Member in the archive | `"strict"` | `"standard"` | `"trusted"` |
|---|---|---|---|
| `../evil.txt`, `/etc/evil.txt` or `C:/evil.txt` | Refused | Refused | Refused |
| A link to `../../outside` or `/etc/passwd` | Refused | Refused | Refused |
| A device file or a FIFO | Refused | Refused | Refused |
| `CON`, `aux.txt` or `file:ads`, which Windows can't create | Refused | Refused | Written as is |
| A name with hidden characters that make an `.exe` look like a `.png` | Refused | Refused | Written as is |
| `notes. `, with a trailing dot and space | Written as `notes` | Written as is | Written as is |
| `caf\xe9.txt`, a name that isn't valid UTF-8 | Written as `caf%E9.txt` | Written as `caf%E9.txt` | Written as is |
| `README`, then `readme` | The second one counts as a file already there | Same as `"strict"` | Both written, if the disk tells them apart |
| A file with mode `rwsr-xr-x` | Written as `rw-r--r--` | Written as `rwxr-xr-x` | Written as is |

`"strict"` and `"standard"` treat `README` and `readme` as the same file on every system, since
they are the same file on macOS and Windows. An archive that writes more than `limits` allows
stops the whole extraction, whatever the policy.

A `filter` can't bring back a member that every policy refuses, because it never sees one. It does
see the members that `"strict"` and `"standard"` refuse for their names alone, such as `CON`, and
if it renames one to a name the policy accepts, that member is written.

A refused member isn't written, and the rest of the archive still extracts. The call returns a
report with one result for each member, with the path it was written to in `result.path` and the
member as the archive stored it in `result.member`. That shows what was refused:

```python
report = archivey.extract("download.zip", "out/")
for result in report:
    if result.status is archivey.ExtractionStatus.BLOCKED:
        print(result.member.name, result.error)
```

If you'd rather stop at the first refused member, `abort_on=["blocked_member"]` raises instead.
