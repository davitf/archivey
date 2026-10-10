# Choosing how to read

With no options, `open_archive` lets you read members in any order, with one member open at a time.
That suits most programs. Three options change it for the cases where the default is slow or not
enough: `streaming`, `seekable_members` and `concurrent_members`. Opening an archive also applies a
few limits, such as how many members it may list, which [Archives you
trust](extracting.md#archives-you-trust) explains how to raise.

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
import sys

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
compressed files can come from one. ZIP and 7z keep their index at the end of the file, and reading
a RAR or an ISO means jumping between places in the file, so all four need a file or another source
that can seek.

## Seeking inside a member

Some code needs to seek inside a member: a library that reads a ZIP stored inside the archive, a
Parquet reader, an image decoder. By default a member stream only moves forward, and `seek` raises.
With `seekable_members=True`, streams from `open` can seek. Moving backwards in compressed data can
mean decompressing the member again from its start. If you install the `seekable` extra, archivey
reads bzip2 data, and large DEFLATE data, through an
[accelerator](security.md#where-the-guarantees-stop) that marks places it can restart from as it
reads, and every seek resumes from the nearest marked place before its target. DEFLATE is the
compression in gzip files and in most ZIP members. Reading a member from start to end lets archivey
notice if its data is damaged, but after a seek, damage may go unnoticed. If you'll seek a lot,
extracting the member to a file first is often faster.

## Several members at once

By default only one member stream can be open at a time, and opening a second while the first
is still open raises. A thread pool that reads different members at once needs
`concurrent_members=True`. Each stream is opened the same way `open` always opens one, so on a
solid archive it may first decompress the members stored before it. The streams share the
source and take turns reading compressed data from it, but they decompress in parallel. That can
be cheaper than opening the archive once per worker, which reads the archive's index each
time. The exception is a source where each jump to a new position is costly, such as a member
of another archive or a file read over the network. There, streams that take turns make the
source jump back and forth, and each jump throws away what it had buffered.

## Why these aren't on by default

Seeking and reading several members at once are off by default, and `streaming=True` turns off
out-of-order reads. Each of these can be slow in some cases, or let damaged data go unnoticed, and
nothing in the calling code shows it. With these defaults, the risky pattern raises instead of
running.

A ZIP can be read out of order at no cost, but a `.tar.gz` can't. If archivey raised only on the
`.tar.gz`, code tested on ZIPs would first fail in production. So archivey raises on every
format, and the mistake shows up during development.

## Summary

| If you want to | Open with | What it costs |
|---|---|---|
| Read a few members by name, or list and then read | nothing | Out-of-order reads can be slow on [solid archives](#solid-archives) |
| Read everything once | `streaming=True` | [One pass only](#reading-once); no reads by name |
| Read from a pipe | `streaming=True` | [TAR and single compressed files only](#reading-once) |
| Seek inside a member | `seekable_members=True` | [A backward seek may decompress again](#seeking-inside-a-member), and damage may go unnoticed after a seek |
| Read members from several threads | `concurrent_members=True` | [Slow on sources where each jump is costly](#several-members-at-once) |
