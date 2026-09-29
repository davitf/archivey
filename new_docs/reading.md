# Choosing how to read

With no options, `open_archive` lets you read any member at any time, one at a time. That
suits most programs. Three options change it for the cases where the default is slow or not
enough: `streaming`, `seekable_members` and `concurrent_members`.

## Solid archives

A ZIP compresses each member on its own, so archivey can jump straight to any of them. A
`.tar.gz` compresses the whole archive as one stream, so reaching a member in the middle means
decompressing everything before it.

An archive whose members are compressed together like this is called *solid*. Every
compressed TAR is solid, and so are most 7z archives, since 7-Zip creates solid archives by
default. RAR archives are solid only when created with that option. On a solid archive,
reading members by name can decompress the same data again and again. `stream_members`
decompresses it once.

## Reading once

```python
with archivey.open_archive(sys.stdin.buffer, streaming=True) as archive:
    for member, stream in archive.stream_members():
        ...
```

`streaming=True` is a promise to read the archive once, from start to end. When set, `open`,
`read` and `members()` raise, so an out-of-order read fails right away instead of quietly
costing time. If you plan to read an archive in one pass, setting it catches that mistake,
even on an ordinary file. You get one pass, through `stream_members` or `extract_all`, and it
is used up even if you leave the loop early.

A pipe, a socket or an HTTP response can only be read this way. Only TAR archives and single
compressed files can come from one. ZIP, 7z, RAR and ISO keep their index at the end of the
file, or jump around in it, so they need a file or another source that can seek.

## Seeking inside a member

Some code needs to seek inside a member: a library that reads a ZIP stored inside the archive,
a Parquet reader, an image decoder. By default a member stream only moves forward, and `seek`
raises. With `seekable_members=True`, streams from `open` can seek. Moving backwards in
compressed data can mean decompressing the member again from its start. Reading a member from
start to end also checks it against the checksum the archive stores, and a seek gives that
check up. If you'll seek a lot, extracting the member to a file first is often faster.

## Several members at once

By default only one member stream can be open at a time, and opening a second while the first
is still open raises. A thread pool that reads different members at once needs
`concurrent_members=True`. Each stream is opened the same way `open` always opens one, so on a
solid archive it may first decompress the members stored before it. The streams also share one
file handle and take turns reading from it. If the archive is a file you can open again,
opening it once per worker gives each worker its own handle, at the cost of reading the
archive's index once per worker.
