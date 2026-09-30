# Extracting

## Extracting all or some members

```python
archivey.extract("download.zip", "out/")
```

`extract` opens the archive, writes every member under the destination folder, and closes it
again. It's safe by default: nothing lands outside `out/`, and an archive that expands far beyond
its size is stopped. [What the default refuses](#what-the-default-refuses) has the details.

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

These are the defaults. `extract_all` takes the same arguments, and the sections below say what
each one does.
