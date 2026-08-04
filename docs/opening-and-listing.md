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

Default open is **random access** (`streaming=False`). On a pipe or other non-seekable
source, either pass a seekable file or use `streaming=True` (forward-only, one pass).

### Damaged archives

`members()` / `scan_members()` assert a **complete** listing and raise on terminal
damage; `members_report()` gives you the recoverable prefix *and* the error together.
Iteration yields the prefix, then raises. See
[Errors and diagnostics](errors-and-diagnostics.md#listing-a-damaged-archive) for the
recipe and what each failure means.

## Detect without opening

```python
info = archivey.detect_format("mystery.bin")
print(info.format, info.confidence)
```

## Passwords

```python
archivey.open_archive("secret.7z", password="hunter2")
archivey.open_archive("secret.zip", password=["likely", "fallback"])
```

List the most likely password first — especially for 7z, where each wrong candidate pays
key derivation.

## Duplicate names and is_current

Appended tarballs, 7z update operations, and similar workflows can produce archives
where **the same member name appears more than once**. Archivey always returns all
entries — `members()` / `__iter__` never hide anything — but marks which one is
"live" with `member.is_current`:

- The **last** entry with a given name has `is_current=True` (last-entry-wins).
- All earlier same-name entries have `is_current=False` (superseded).

`extract_all` honours this automatically: non-current entries get
`ExtractionStatus.SUPERSEDED` (distinct from overwrite `NOT_OVERWRITTEN`) and are not written,
so the final on-disk state matches what you would get from a fresh write.

To enumerate only the live state in your own code, filter with a one-liner:

```python
with archivey.open_archive("updated.tar") as reader:
    current = [m for m in reader if m.is_current]
```

If you need all versions (e.g. a history view), iterate without filtering:

```python
with archivey.open_archive("history.tar") as reader:
    for member in reader:
        tag = "" if member.is_current else " [superseded]"
        print(f"{member.name}{tag}")
```
