# How it works

You don't need this page to use archivey. It's for when you want to know what's underneath. Maybe
you're deciding whether to trust archivey with archives from strangers and want to know which code
reads them. Maybe an error names a package or program you didn't expect, or a file was recognized
as a different format than you thought. The page follows what happens when you open an archive and
read from it: how archivey recognizes the format, reads the archive's structure, and decodes the
data. For why the defaults are the way they are, see [Philosophy](philosophy.md).

## Recognizing the format

Archivey decides the format from the file's contents first. Most formats start with a fixed
signature. Some keep it further in, as ISO images do at 32 KiB, or behind a program stub, as
self-extracting archives do. A single compressed file such as a `.gz` is also checked for a TAR
header inside, so a tarball named `.gz` opens as a `.tar.gz`. The name is used only when the
contents don't settle it.

A few formats, such as raw zlib, LZMA alone and Brotli, have no signature at all. Archivey
recognizes them by trying to decode the start of the data, which is called a probe. Almost any
bytes can pass for the start of one of these streams, so a probe runs only when the file's
extension names that format, when you set `always_probe_content=True`, or when you call
`open_stream()`. `detect_format()` runs the same steps without opening the archive.

## Reading the archive's structure

Once the format is known, a parser reads the archive's structure: the list of members, their names,
sizes and times, and where each one's data starts. Archivey has its own parsers for ZIP, TAR, 7z and
RAR, and uses pycdlib for ISO images. All of them are written in Python, so a malicious archive can
make a parser give a wrong answer but can't corrupt memory. [Security](security.md) covers the code
that can, the decompressors.

Its own parsers mean every format follows the same rules for metadata, and any archive, solid ones
included, can be read in a single forward pass with `stream_members()`.

In archivey's tests, the 7z and RAR parsers are checked against py7zr, rarfile and `unrar`,
comparing both metadata and bytes. None of those libraries is used at run time. For each format's
details, see the [format handbooks](https://github.com/davitf/archivey/tree/main/dev-docs/formats)
and the [library analysis](https://github.com/davitf/archivey/blob/main/dev-docs/library-analysis.md),
both written for people working on archivey itself.
