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

## Opening an archive

`open_archive` opens every format archivey reads and identifies it from the file's contents, so a
TAR file named `backup.bin` opens too, where `shutil.unpack_archive` fails because of the extension.
If your code actually needs to handle only one format, you can pass `format="zip"` or
`format=archivey.ArchiveFormat.TAR_GZ` to skip detection. A file in another format then fails like a
damaged one ([Format](opening.md#format)).

A password is given once, when opening: one string, a list of candidates, or a function that
supplies them ([Passwords](opening.md#passwords)). zipfile takes one per read or through
`setpassword`, and only for the older ZIP encryption. Archivey also reads WinZip AES, and 7z and RAR
archives with a password.

Three options to `open_archive` allow ways of reading that are off by default:

| To do this | zipfile | tarfile | archivey |
| --- | --- | --- | --- |
| Seek in a member | Allowed; a seek back decompresses again from the start | Allowed; the same | `seekable_members=True`; with the [`seekable` extra](reading.md#seeking-inside-a-member), seeks in bzip2 and large gzip data restart near their target |
| Keep several members open at once | Allowed | Allowed | `concurrent_members=True` |
| Read from a pipe | Not possible | One pass, with mode `"r\|*"` | One pass, with `streaming=True`, for TAR and single compressed files |

Seeking and keeping several members open are cheap on some archives and slow on others, and nothing
in the calling code shows which. Archivey turns them off by default so that code tested on a ZIP
doesn't first turn slow on a `.tar.gz` in production. [Choosing how to read](reading.md) explains
each option and what it costs.

## Members and their details

Every format gives an [`ArchiveMember`](api.md#archivey.ArchiveMember) with the same fields. The
ones code using `ZipInfo` or `TarInfo` reads most map like this:

| zipfile | tarfile | archivey |
| --- | --- | --- |
| `info.filename` | `info.name` | `member.name` |
| `info.file_size` | `info.size` | `member.size` |
| `info.compress_size` | | `member.compressed_size` |
| `info.date_time` | `info.mtime` | `member.modified` |
| `info.external_attr >> 16` | `info.mode` | `member.mode` |
| `info.is_dir()` | `info.isdir()`, `info.isfile()`, `info.issym()` | `member.is_dir`, `member.is_file`, `member.is_link` |
| | `info.linkname` | `member.link_target` |

A field the archive doesn't record is `None`, such as `compressed_size` for a member of a
`.tar.gz`.

`member.modified` is a `datetime` holding what the archive stores. A ZIP records local time with no
time zone, which is why zipfile gives a plain tuple, so `modified` has no time zone either. A TAR
records UTC, which tarfile gives as a timestamp, and `modified` is then a UTC `datetime`.
`member.modified_utc()` gives UTC for both, and treats a time with no zone as your local time unless
you pass another zone.

When an archive holds a name twice, zipfile, tarfile and archivey all list it twice, and reading by
name gives the last copy. Going through the member list, `member.is_current` tells you which copy
that is ([Members with the same name](opening.md#members-with-the-same-name)).
