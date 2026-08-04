# Migrating from zipfile, tarfile, shutil, and patool

Recipes for replacing the tool you're using now. Each section shows the old code, the
Archivey equivalent, and — where it matters — what actually changes in behaviour.

The recurring theme: the stdlib modules are per-format and extract permissively by
default; Archivey is one interface for every format and **blocks unsafe members unless
you opt out**. Most migrations are shorter code plus stricter defaults.

## Cheat sheet

| You're doing | Now | With Archivey |
| --- | --- | --- |
| Open an archive | `zipfile.ZipFile(p)` / `tarfile.open(p)` | `archivey.open_archive(p)` |
| List names | `zf.namelist()` / `tf.getnames()` | `[m.name for m in reader]` |
| Member metadata | `ZipInfo` / `TarInfo` | `ArchiveMember` (same shape for every format) |
| Read one member | `zf.read(n)` / `tf.extractfile(n).read()` | `reader.read(n)` |
| Extract everything | `zf.extractall(d)` / `tf.extractall(d, filter="data")` | `archivey.extract(p, d)` |
| Unpack any format | `shutil.unpack_archive(p, d)` | `archivey.extract(p, d)` |
| Decompress a `.gz` | `gzip.open(p)` | `archivey.open_stream(p)` |
| Detect the format | guess from the extension | `archivey.detect_format(p)` |

## From `zipfile`

```python
# Before
import zipfile
with zipfile.ZipFile("photos.zip") as zf:
    for name in zf.namelist():
        print(name, zf.getinfo(name).file_size)
    data = zf.read("subdir/a.txt")
    zf.extractall("out/")

# After
import archivey
with archivey.open_archive("photos.zip") as reader:
    for member in reader:
        print(member.name, member.size)
    data = reader.read("subdir/a.txt")
    reader.extract_all("out/")
```

What changes:

- **`extractall` was never safe.** `zipfile.extractall` sanitizes absolute paths and `..`
  by mangling them, but happily writes symlinks that point outside the destination.
  `extract_all` **blocks** traversal and symlink escapes by default and reports them as
  `ExtractionStatus.BLOCKED`. See [Safe extraction](extracting.md).
- **Passwords are an open-time argument**, not per-call `pwd=`:
  `open_archive("secret.zip", password="hunter2")`. Archivey also reads **WinZip AES**
  members (with the `[recommended]` extra), which `zipfile` cannot decrypt at all.
- **Duplicate names are visible.** `namelist()` returns duplicates with no way to tell
  which one wins; Archivey marks the live entry with `member.is_current`. See
  [duplicate names](opening-and-listing.md#duplicate-names-and-is_current).
- `reader.read(name)` needs no `getinfo` round-trip, and works the same on every format.

## From `tarfile`

```python
# Before
import tarfile
with tarfile.open("backup.tar.gz") as tf:
    for info in tf:
        print(info.name, info.size)
    with tf.extractfile("etc/config") as f:
        data = f.read()
    tf.extractall("out/", filter="data")

# After
import archivey
with archivey.open_archive("backup.tar.gz") as reader:
    for member in reader:
        print(member.name, member.size)
    data = reader.read("etc/config")
    reader.extract_all("out/")
```

What changes:

- **You no longer choose a filter.** `tarfile`'s `filter="data"` (the 3.14 default, and a
  `DeprecationWarning` before that) is closest to Archivey's default
  `ExtractionPolicy.STRICT`. Archivey's policies are
  `STRICT` / `STANDARD` / `TRUSTED` and apply to *every* format, not just tar.
- **`extractfile` can return `None`** for non-regular members, so stdlib code needs a
  `None` check; `reader.read(name)` raises a typed error instead.
- **Compressed tars are solid.** `tarfile` lets you call `extractfile` in any order and
  silently re-decompresses from the start each time — that's the classic accidental
  O(n²). Archivey makes the cost visible via `reader.cost` and steers you to
  `stream_members()`. See [Access costs](access-and-cost.md).
- **Truncated archives.** `tarfile` often stops silently at a short read. Archivey gives
  you the recovered prefix *and* the error via `members_report()`.

## From `shutil.unpack_archive`

```python
# Before
import shutil
shutil.unpack_archive("bundle.tar.xz", "out/")

# After
import archivey
archivey.extract("bundle.tar.xz", "out/")
```

What changes:

- **More formats.** `unpack_archive` handles zip/tar family only. Archivey adds RAR, 7z,
  ISO, and single-file streams through the same call.
- **Format comes from content, not the filename.** `unpack_archive` dispatches on the
  extension and fails on a misnamed file; Archivey sniffs the bytes.
- **Safe by default**, with the same policy knobs as `extract_all`.
- You get an `ExtractionReport` back — what was written, skipped, or blocked — instead of
  `None`.

## From `patool` / shelling out to `7z` / `unrar`

```python
# Before
subprocess.run(["7z", "x", "-o" + dest, "archive.7z"], check=True)

# After
import archivey
archivey.extract("archive.7z", dest)
```

What changes:

- **No external binary for 7z**, and no CLI output parsing. Archivey has a native 7z
  reader (common codecs in the core; PPMd/Deflate64 and AES via `[recommended]`).
- **RAR still needs `unrar`** for member *data* — metadata and listing are native. That is
  a licensing constraint, not an oversight: the RAR decompression algorithm may not be
  reimplemented, so `unrar` stays in the picture for data while metadata does not need it.
- **Errors are exceptions, not exit codes**, and hostile archives can't reach a shell:
  the whole point is not handing untrusted filenames to a subprocess.
- Wrong passwords raise `EncryptionError` rather than prompting on a tty and hanging.

## From `py7zr` / `rarfile`

Both remain useful as *writers* and as cross-check oracles; Archivey does not write 7z or
RAR. For reading:

```python
# Before
import py7zr
with py7zr.SevenZipFile("a.7z") as z:
    z.extractall("out/")

# After
import archivey
archivey.extract("a.7z", "out/")
```

The reason to switch is memory safety and uniformity: Archivey parses 7z and RAR metadata
in pure Python rather than delegating to a third-party parser, and the same reader
interface covers every other format you handle.

## Things that will bite you

Worth reading before you migrate a production path:

1. **Extraction is strict.** Archives that "worked" with `extractall` may now report
   `BLOCKED` members. That is the point — but check the
   [`ExtractionReport`](extracting.md) rather than assuming success.
2. **One live member stream by default.** If you held several `extractfile()` handles
   open, pass `concurrent_members=True`. See
   [supported platforms and threading](support-matrix.md).
3. **`read()` is all-or-raise.** A truncated member raises instead of returning a short
   body; use a chunked loop if you want the recoverable prefix
   ([reading members](reading-members.md#read-a-member)).
4. **Random access on a pipe fails loudly** instead of silently buffering the whole thing
   into memory — an unbounded allocation you did not ask for is worse than an error.
   Pass `streaming=True` for a forward-only pass.
5. **Salvage is not implemented yet.** Reading a badly damaged archive gives you the
   recoverable prefix plus an honest error, not a best-effort resync past the damage.
