# Migrating from other libraries

Archivey reads ZIP, TAR, 7z, RAR and ISO archives, and single compressed files, through one
interface, so code written for zipfile, tarfile or a `7z` subprocess becomes the same few calls.
This page gives the archivey version of each, then what behaves differently. The difference you'll
meet first is in extraction: archivey refuses some members that zipfile or tarfile would write, and
it stops an archive that expands past its limits.

## Equivalent calls

| To do this | With the standard library | With archivey | What's different |
| --- | --- | --- | --- |
| Open an archive | `zipfile.ZipFile(p)`, `tarfile.open(p)` | `archivey.open_archive(p)` | [Any format, identified from its contents, with options for how you'll read it](#opening-an-archive) |
| List the names | `zf.namelist()`, `tf.getnames()` | `[m.name for m in reader]` | [A name can appear more than once](#members-and-their-details) |
| Look up one member | `zf.getinfo(n)`, `tf.getmember(n)` | `reader.get(n)` | Returns an [`ArchiveMember`](api.md#archivey.ArchiveMember) |
| Read one member | `zf.read(n)`, `tf.extractfile(n).read()` | `reader.read(n)` | [A directory raises an error](#reading-members) |
| Open one member as a file object | `zf.open(n)`, `tf.extractfile(n)` | `reader.open(n)` | [One open at a time, and not seekable, by default](#reading-members) |
| Read every member, in order | `for info in tf: tf.extractfile(info)` | `for member, stream in reader.stream_members():` | [One pass over the archive, in every format](#reading-members) |
| Give a password | `zf.read(n, pwd=b"...")` | `archivey.open_archive(p, password="...")` | [Given once, when opening, and can be a list](#opening-an-archive) |
| Extract everything | `zf.extractall(d)`, `tf.extractall(d)` | `reader.extract_all(d)` | [Unsafe members are refused, and policies and filters choose what's written](#extracting) |
| Unpack any supported format | `shutil.unpack_archive(p, d)` | `open_archive(p)`, then `reader.extract_all(d)` | [More formats, and a report of what was written](#extracting) |
| Decompress a single file | `gzip.open(p)`, `bz2.open(p)`, `lzma.open(p)` | `archivey.open_stream(p)` | [One call for every compression format](opening.md#single-compressed-files) |
| Identify the format | the file's extension | `archivey.detect_format(p)` | [Reads the contents](opening.md#format) |
