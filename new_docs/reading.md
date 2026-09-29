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
solid archive it may first decompress the members stored before it. The streams share the
source and take turns reading compressed data from it, but they decompress in parallel. That is
usually cheaper than opening the archive once per worker, which reads the archive's index each
time. The exception is a source where each jump to a new position is costly, such as a member
of another archive or a file read over the network. There, streams that take turns make the
source jump back and forth, and each jump throws away what it had buffered.

## Why these aren't on by default

Seeking and reading several members at once are off by default, and `streaming=True` turns off
out-of-order reads. Each of these can be slow in some cases, or skip a check, in ways the code
doesn't show. With the checks in place, the risky pattern raises instead of running.

A ZIP can be read out of order at no cost, but a `.tar.gz` can't. If archivey raised only on the
`.tar.gz`, code tested on ZIPs would first fail in production. So the checks apply on every
format, and the mistake shows up during development.
