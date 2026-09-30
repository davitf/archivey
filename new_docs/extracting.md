# Extracting

## Extracting all or some members

```python
archivey.extract("download.zip", "out/")
```

`extract` opens the archive, writes every member under the destination folder, and closes it
again. It's safe by default: nothing lands outside `out/`, and an archive that expands far beyond
its size is stopped. [What is refused](#what-is-refused) has the details.

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
    limits=archivey.ExtractionLimits(max_extracted_bytes=2 * 2**30),  # how much it may write
)
```

These are the defaults, and `extract_all` takes the same arguments. `policy` decides how much of
what the archive says about names and permissions gets written as it is:

| `policy` | Names | Permissions |
|---|---|---|
| `"strict"` (default) | Rewritten to a portable spelling; names built to look like something else are refused | Files at most `rw-r--r--` and never executable, folders at most `rwxr-xr-x` |
| `"standard"` | As in `"strict"`, but trailing dots and spaces are kept | As stored, without setuid, setgid and sticky bits |
| `"trusted"` | As stored | As stored, and the owner too when running as root |

`overwrite` decides what happens when a file is already where a member would go:

| `overwrite` | Effect |
|---|---|
| `"error"` (default) | The member fails |
| `"skip"` | The existing file stays, and the member is skipped |
| `"replace"` | The existing file is deleted, and the member is written |
| `"rename"` | The member is written next to it, as `name (1)` |

`on_error` decides what a failed member does to the rest of the extraction:

| `on_error` | Effect |
|---|---|
| `"stop"` (default) | The first failure raises, and extraction stops there |
| `"continue"` | The failure is recorded in the report, and extraction goes on |

## What is refused

Under every policy, `extract` refuses any member that would end up outside the destination folder:
names with `../`, absolute paths, and links that point outside it, even through other links. It
also refuses device files. By default it refuses names with hidden characters that flip the text
after them, which can make an `.exe` file look like a `.png`, and only `policy="trusted"` lets them
through. An archive that writes more than `limits` allows stops the whole extraction.

A refused member isn't written, and the rest of the archive still extracts. The call returns a
report with one result for each member, so you can see what was refused:

```python
report = archivey.extract("download.zip", "out/")
for result in report:
    if result.status is archivey.ExtractionStatus.BLOCKED:
        print(result.member.name, result.error)
```

If you'd rather stop at the first refused member, `abort_on=["blocked_member"]` raises instead.

## Names can change on disk

Some names can't be written as they are on every system, so by default archivey writes a portable
spelling instead. Bytes that aren't valid UTF-8 become `%` escapes, and trailing dots and spaces
are removed, since Windows drops them. Names that differ only in case, like `README` and `readme`,
count as the same file on every system, because on macOS and Windows they are. The second one is
handled like a file that's already there (next section).

Each result in the report has the path that was written in `result.path`, next to the member with
its name as the archive stored it, in `result.member.name`.
