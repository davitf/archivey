# Opening and listing

Point Archivey at something, get past whatever guards it, and find out what is
inside. Reading the bytes out is [Reading members](reading-members.md).

## Open and list

```python
import archivey

with archivey.open_archive("photos.zip") as reader:
    for member in reader:                    # archive order
        print(member.name, member.size, member.type)

    members = reader.members()               # complete list, or raise
    info = reader.get("subdir/a.txt")        # by name
    print(reader.format, reader.cost)
```

You get random access by default. If your source is a pipe or anything else you
cannot seek in, pass `streaming=True` for a forward-only single pass — otherwise
the open fails immediately rather than halfway through.

## What you can open

| Source | What happens |
|---|---|
| A path to a file | Opened and detected as usual |
| A path to a directory | Opens as a pseudo-archive, one member per file. A directory always opens as a directory, even if you pass `format=` |
| An open binary stream | See below |
| A sequence of paths or streams | The volumes of one multi-volume archive, in order. **7z and RAR only** — anything else raises. A one-item sequence is just that item |

Streams have one rule worth knowing: **a seekable stream is read from wherever it
currently is.** Archivey treats that position as byte 0 of the archive, so an
archive embedded in a larger file needs no slicing — seek to where it starts and
hand the stream over. A non-seekable stream is fine too, with `streaming=True`;
Archivey buffers what it peeks at during detection and replays it, so detection
never eats bytes the archive needs.

## Detection

```python
info = archivey.detect_format("mystery.bin")
print(info.format, info.confidence)
```

**Content wins over filename.** Archivey looks at the bytes first and falls back to
the extension only when they are inconclusive. When the two disagree it uses the
bytes and tells you, via a `FORMAT_EXTENSION_CONFLICT`
[diagnostic](errors-and-diagnostics.md) naming both candidates — a `.jpg` that is
really a ZIP opens fine, and you can still find out that the name lied.

One disagreement is not reported, because it is not one: a `foo.tar.gz` may come
back as plain `GZ`. Deciding whether a compressed stream contains a tar means
looking inside it, and detection skips that when the decompressor isn't installed.
The question is settled when you open the file.

That is also the difference between the two entry points on the same `.gz`:

```python
archivey.open_archive("logs.tar.gz")   # the files inside the tar
archivey.open_stream("access.log.gz")  # the decompressed bytes, as a stream
```

`open_archive` works on a plain `.gz` as well — you get an archive with exactly one
member, named after the file.

## Passwords

```python
archivey.open_archive("secret.7z", password="hunter2")
archivey.open_archive("secret.zip", password=["likely", "fallback"])
```

Put the most likely password first. Every wrong candidate costs work before it is
rejected, and on 7z that work is expensive key derivation.

Passing a password to a format that has no encryption at all — a tar, say — raises
`UnsupportedOperationError` rather than ignoring it, since it usually means the call
is not doing what you think. A `PasswordProvider` callable is exempt: it is only
called if something actually asks for a password.

## Damaged archives

`members()` / `scan_members()` assert a **complete** listing and raise on terminal
damage; `members_report()` gives you the recoverable prefix *and* the error together.
Iteration yields the prefix, then raises. See
[Errors and diagnostics](errors-and-diagnostics.md#listing-a-damaged-archive) for the
recipe and what each failure means.

## Duplicate names and is_current

Appending to a tarball, or updating a 7z, can leave **the same member name in the
archive more than once**. Archivey never hides the older copies — `members()` and
iteration return every entry — but it marks which one is live:

- The **last** entry with a given name has `is_current=True`.
- Earlier entries with that name have `is_current=False`.

`extract_all` already follows this: superseded entries are skipped and reported as
`ExtractionStatus.SUPERSEDED` (not to be confused with `NOT_OVERWRITTEN`, which is
about files already on disk), so what lands on disk matches a fresh write.

In your own code, filter for the live state:

```python
with archivey.open_archive("updated.tar") as reader:
    current = [m for m in reader if m.is_current]
```

or don't, if you want the history:

```python
with archivey.open_archive("history.tar") as reader:
    for member in reader:
        tag = "" if member.is_current else " [superseded]"
        print(f"{member.name}{tag}")
```
