# Migrating from other libraries

Archivey reads ZIP, TAR, 7z, RAR and ISO archives, and single compressed files, through one
interface, so code written for zipfile, tarfile or a `7z` subprocess becomes the same few calls.
This page gives the archivey version of each, then what behaves differently. Archivey puts
correctness and the same behaviour in every format ahead of matching any one library. The difference
you'll meet first is in extraction: archivey refuses some members that zipfile or tarfile would
write, and it stops an archive that expands past its limits.

## Equivalent calls

| To do this | With the standard library | With py7zr or rarfile | With archivey | What's different |
| --- | --- | --- | --- | --- |
| Open an archive | `zipfile.ZipFile(p)`, `tarfile.open(p)` | `py7zr.SevenZipFile(p)`, `rarfile.RarFile(p)` | `archivey.open_archive(p)` | [Any format, identified from its contents, with options for how you'll read it](#opening-an-archive) |
| List the names | `zf.namelist()`, `tf.getnames()` | `z.getnames()`, `rf.namelist()` | `[m.name for m in reader]` | [A name can appear more than once](#members-and-their-details) |
| Look up one member | `zf.getinfo(n)`, `tf.getmember(n)` | `z.getinfo(n)`, `rf.getinfo(n)` | `reader.get(n)` | Returns an [`ArchiveMember`](api.md#archivey.ArchiveMember) |
| Read one member | `zf.read(n)`, `tf.extractfile(n).read()` | `z.extract(targets=[n], factory=f)`, `rf.read(n)` | `reader.read(n)` | [A directory raises an error](#reading-members) |
| Open one member as a file object | `zf.open(n)`, `tf.extractfile(n)` | `rf.open(n)` | `reader.open(n)` | [One open at a time, and not seekable, by default](#reading-members) |
| Read every member, in order | `for info in tf: tf.extractfile(info)` | `z.extractall(factory=f)` | `for member, stream in reader.stream_members():` | [One pass over the archive, in every format](#reading-members) |
| Give a password | `zf.read(n, pwd=b"...")` | `SevenZipFile(p, password="...")`, `rf.setpassword("...")` | `archivey.open_archive(p, password="...")` | [Given once, when opening, and can be a list](#opening-an-archive) |
| Extract everything | `zf.extractall(d)`, `tf.extractall(d)` | `z.extractall(d)`, `rf.extractall(d)` | `reader.extract_all(d)` | [Unsafe members are refused, and policies and filters choose what's written](#extracting) |
| Unpack any supported format | `shutil.unpack_archive(p, d)` | | `open_archive(p)`, then `reader.extract_all(d)` | [More formats, and a report of what was written](#extracting) |
| Decompress a single file | `gzip.open(p)`, `bz2.open(p)`, `lzma.open(p)` | | `archivey.open_stream(p)` | [One call for every compression format](opening.md#single-compressed-files) |
| Identify the format | `zipfile.is_zipfile(p)`, `tarfile.is_tarfile(p)` | | `archivey.detect_format(p)` | [Names the format, among all those archivey reads](opening.md#format) |

## Opening an archive

`open_archive` opens every format archivey reads and identifies it from the file's contents, so a
TAR file named `backup.bin` opens too, where `shutil.unpack_archive` fails because of the extension.
If your code actually needs to handle only one format, you can pass `format="zip"` or
`format=archivey.ArchiveFormat.TAR_GZ` to skip detection. A file in another format then fails like a
damaged one ([Format](opening.md#format)).

A password is given once, when opening: one string, a list of candidates, or a function that
supplies them ([Passwords](opening.md#passwords)). zipfile takes one per read or through
`setpassword`, and only for the older ZIP encryption. With the `recommended` extra, archivey also
reads WinZip AES. py7zr takes the password when opening, like archivey, and rarfile takes it per
read or through `setpassword`.

Like rarfile, archivey reads files stored without compression in a RAR archive on its own, and needs
a program for compressed ones: RARLAB's `unrar` or `rar`, or `unar` when neither is installed.
rarfile can also use `7z` or `bsdtar`, and archivey can't.

Three options to `open_archive` allow ways of reading that are off by default:

| To do this | zipfile | tarfile | py7zr | rarfile | archivey |
| --- | --- | --- | --- | --- | --- |
| Seek in a member | Allowed; a seek back decompresses again from the start | Allowed; the same | No member streams | Allowed; the same | `seekable_members=True`; with the [`seekable` extra](reading.md#seeking-inside-a-member), seeks in bzip2 and large gzip data restart near their target |
| Keep several members open at once | Allowed | Allowed | No member streams | Allowed | `concurrent_members=True` |
| Read from a pipe | Not possible | One pass, with mode `"r\|*"` | Not possible | Not possible | One pass, with `streaming=True`, for TAR, ZIP and single compressed files |

Seeking and keeping several members open are cheap on some archives and slow on others, and nothing
in the calling code shows which. Archivey turns them off by default so that code tested on a ZIP
doesn't first turn slow on a `.tar.gz` in production. [Choosing how to read](reading.md) explains
each option and what it costs.

## Members and their details

Every format gives an [`ArchiveMember`](api.md#archivey.ArchiveMember) with the same fields. The
ones code using `ZipInfo` or `TarInfo` reads most map like this:

| zipfile | tarfile | py7zr | rarfile | archivey |
| --- | --- | --- | --- | --- |
| `info.filename` | `info.name` | `info.filename` | `info.filename` | `member.name` |
| `info.file_size` | `info.size` | `info.uncompressed` | `info.file_size` | `member.size` |
| `info.compress_size` | | `info.compressed` | `info.compress_size` | `member.compressed_size` |
| `info.date_time` | `info.mtime` | `info.creationtime` | `info.mtime` | `member.modified` |
| `info.external_attr >> 16` | `info.mode` | | `info.mode` | `member.mode` |
| `info.is_dir()` | `info.isdir()`, `info.isfile()`, `info.issym()` | `info.is_directory`, `info.is_file`, `info.is_symlink` | `info.is_dir()`, `info.is_file()`, `info.is_symlink()` | `member.is_dir`, `member.is_file`, `member.is_link` |
| | | | `info.ctime` | `member.created` or `member.ctime` |
| | `info.linkname` | | | `member.link_target` |

zipfile doesn't recognize symbolic links: a ZIP entry that records one is a plain file to it, and
`extractall` writes the target's path as the file's contents. Archivey reports it as a link, with
`member.is_link` and `member.link_target`.

A field the archive doesn't record is `None`, such as `compressed_size` for a member of a
`.tar.gz`. A directory's name ends with `/` in every format, so a directory tarfile lists as `docs`
is `docs/` in archivey. `reader.get` returns `None` for a name that isn't there, where `getinfo` and
`getmember` raise `KeyError`.

`member.modified` is a `datetime` with the time zone the archive records. Some ZIPs, such as those
made by the `zip` command, carry a UTC time beside the old local one. zipfile ignores it, and
archivey uses it, so `modified` is in UTC there. A ZIP without it, or a RAR 4 archive, records a
local time with no zone, and `modified` has no zone either. TAR, 7z and RAR 5 archives record UTC:
tarfile gives a number of seconds, and py7zr, rarfile and archivey give a UTC `datetime`. py7zr's
`creationtime` holds this modification time. `member.modified_utc()` gives UTC for every format, and
treats a time with no zone as your local time unless you pass another zone.

RAR, 7z and ZIP archives have a slot for a creation time, and what goes in it depends on where the
archive was made. Tools on Windows store when the file was created, and tools on Linux and macOS
store when its metadata last changed, Unix's `ctime`. rarfile gives the slot as `ctime` whatever it
holds. Archivey puts a creation time in `member.created` and a change time in `member.ctime`, so
`created` never holds a change time.

When an archive holds a name twice, zipfile, tarfile and archivey all list it twice, and reading by
name gives the last copy. Going through the member list, `member.is_current` tells you which copy
that is ([Members with the same name](opening.md#members-with-the-same-name)).

## Reading members

`reader.read` and `reader.open` work like their zipfile and tarfile counterparts, in every format
archivey opens. For ZIP, the `recommended` extra adds compression methods zipfile can't read:
Deflate64, PPMd, and zstd before Python 3.14.

Calling `read` or `open` on a directory raises `ArchiveyUsageError`, where zipfile returns empty
bytes and tarfile's `extractfile` returns `None`. Reading a symbolic link gives its target's
contents, as tarfile does, and raises `LinkTargetNotFoundError` when the target isn't in the
archive, where tarfile raises `KeyError`. zipfile gives the link's own text, the target's path.

In a `.tar.gz`, in most 7z archives and in some RAR archives, members are compressed together, so
reading one member on its own means decompressing everything before it. tarfile avoids repeating
that work when you read each member while iterating over the archive, and py7zr when you pass all
the names to one `extract` call. rarfile has no way to avoid it. With archivey, use
`stream_members()` whenever you need several members and their order doesn't matter: it reads each
member once, in order, in every format ([Solid archives](reading.md#solid-archives)). `read` and
`open` cost more. In a `.tar.gz`, the first one lists the whole archive, which takes a pass of its
own, and reading members in the archive's order after that takes one more pass. Going back to an
earlier member starts again from the beginning. In 7z and RAR, each read decompresses again
everything before its member. If you want to make sure your code avoids these costs, open the
archive with `streaming=True`: `read` and `open` then always raise, and `stream_members()` is the
only way to read members.

## Extracting

`extract_all` never writes outside the destination folder, in any format or under any policy.
zipfile and tarfile treat the same members differently:

| Member | zipfile | tarfile with `filter="data"` | archivey, `strict` (the default) | archivey, `standard` |
| --- | --- | --- | --- | --- |
| `/abs.txt` | Written as `abs.txt` | Written as `abs.txt` | Refused | Written as `abs.txt` |
| `../up.txt` | Written as `up.txt` | Raises, ending the extraction | Refused | Refused |
| Link to `../../etc/passwd` | A plain file holding the path | Raises, ending the extraction | Refused | Refused |
| File with mode 4755 (setuid) | Not kept | 755 | 644 | 755 |
| File with mode 666 | Not kept | 644 | 644 | 666 |
| File with mode 755 | Not kept | 755 | 644 | 755 |

zipfile ignores the permissions an archive records, so each file gets the system's default for new
files, usually 644. py7zr raises on `../up.txt` and on a link pointing outside, and rarfile renames
`../up.txt` and `/abs.txt` as zipfile does and raises on the link. Both keep the recorded
permissions. The data filter is tarfile's default from Python 3.14. Before that, tarfile with
no filter writes every member as recorded, outside the destination too, and Python 3.12 and 3.13
warn about it. The Extracting page has [a longer
table](extracting.md#what-each-policy-does-with-unusual-members), with more kinds of member and
every policy.

When archivey refuses a member, it skips it and goes on with the others, where tarfile's data
filter raises and ends the whole extraction. `abort_on=["blocked_member"]` makes archivey stop too.
If you want zipfile's renaming, pass `filter=archivey.sanitize_names`. `filter=` also takes your own
function, which can rename, skip or change each member ([Extracting all or some
members](extracting.md#extracting-all-or-some-members)).

zipfile and tarfile write as much as the archive holds. Archivey stops at 2 GiB written, 262,144
members, or a member that expands more than 1000 times, and all three can be changed ([Archives you
trust](extracting.md#archives-you-trust)). `extract_all` returns an `ExtractionReport` saying what
happened to each member, where `extractall` returns `None`.

`shutil.unpack_archive` picks the format from the extension, handles ZIP and TAR (with gzip, bzip2,
xz, and zstd from Python 3.14), and extracts them through zipfile and tarfile, so the comparison
above applies to it too.

## Errors

Every problem with the archive raises a subclass of `archivey.ArchiveyError`, so one `except` covers
what took several exception types before:

| Problem | zipfile | tarfile | py7zr | rarfile | archivey |
| --- | --- | --- | --- | --- | --- |
| Not an archive | `BadZipFile` | `ReadError` | `Bad7zFile` | `NotRarFile` | `FormatDetectionError` |
| Damaged or cut short | `BadZipFile` | `ReadError`, or `EOFError` from the decompressor | `Bad7zFile`, `CrcError` | `BadRarFile` | `CorruptionError`, or `TruncatedError` when cut short |
| Wrong password | `RuntimeError` | | `LZMAError` | `BadRarFile`, or `RarWrongPassword` when the names are encrypted too | `EncryptionError` |
| No member with that name | `KeyError` | `KeyError` | `KeyError` | `NoRarEntry` | `KeyError` |

A missing name raises `KeyError`, which isn't an `ArchiveyError`, because the mistake is in the
calling code, not the archive. [Errors and diagnostics](errors-and-diagnostics.md) explains each
exception, and the warnings archivey reports without raising.
