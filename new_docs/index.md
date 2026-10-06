# Archivey

Archivey reads ZIP, TAR, 7z, RAR, ISO and single compressed files from Python, with one
simple API. Untrusted archives extract safely by default. Archive formats hide some slow and
error-prone ways of reading, and archivey's defaults steer around them whenever possible.

## Install

Install the `archivey` package from PyPI with your usual tool (e.g.
`uv add "archivey[recommended]"` or `pip install "archivey[recommended]"`). Without
`recommended`, archivey has no dependencies and reads ZIP, TAR, 7z with its common compression
methods, and the single compressed files the standard library handles, such as gzip, bzip2 and
xz. The extra adds ISO, lz4, Brotli, zstd on Python 3.13 and older, the rarer compression methods in 7z and ZIP, and
AES decryption, which most encrypted 7z and ZIP archives need.

RAR archives can be listed with nothing else installed. To read the files inside, you also
need RARLAB's `unrar` 6.0 or later, or `unar` 1.10 or later, which handles fewer RAR archives.
[Install](install.md) has the details.

## Open and list

```python
import archivey

with archivey.open_archive("photos.zip") as archive:
    for member in archive:
        print(member.name, member.size)
```

`open_archive` identifies the format from the file's contents, so the same code opens a `.7z`
or a `.tar.gz`. Each entry in the archive, whether a file, a directory or a link, is a
*member*, as in `zipfile` and `tarfile`. [`ArchiveMember`](api.md#archivey.ArchiveMember)
lists everything a member carries.

## Read a member

```python
with archivey.open_archive("photos.zip") as archive:
    data = archive.read("holiday/beach.jpg")
```

`read` returns the whole member as bytes. For a large member, `open` gives you a file object
to read in pieces:

```python
import shutil

with archive.open("holiday/video.mp4") as stream, open("video.mp4", "wb") as out:
    shutil.copyfileobj(stream, out)
```

On some archives, reading a member means first decompressing the members stored before it, so
reading them out of order gets slow. [Solid archives](reading.md#solid-archives) explains when this happens. The
next section shows how to avoid it by reading them all in one pass.

## Read everything in one pass

```python
with archivey.open_archive("backup.tar.gz") as archive:
    for member, stream in archive.stream_members():
        if stream is None:
            continue  # a directory or a link
        process(member.name, stream.read())
```

`stream_members` goes through the archive in the order it is stored and gives you each member
with a stream of its contents. It reads each member once, so it is never slower than reading
them by name, and on some archives it is much faster. Directories and links come with `None` in
place of a stream, and each stream works only until the loop moves on.

## Extract

```python
archivey.extract("download.zip", "out/")
```

`extract` writes every member under `out/`. By default it refuses anything that would land
outside that folder, such as `../` paths, absolute paths or links pointing out of it, and it
stops archives that expand to far more data than they hold. [Extracting](extracting.md)
lists every protection and how to relax them for archives you trust, and also shows how to
extract only some files.

## When something goes wrong

Problems with the archive raise a subclass of `ArchiveyError`: `CorruptionError` for damaged
data, `EncryptionError` for a missing or wrong password, `PackageNotInstalledError` when a
format needs the extra. Archivey raises these the same way for every format, including where
the underlying library would stop quietly and hand back short data.
[Errors](errors-and-diagnostics.md) has the full list.
