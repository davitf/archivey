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

zipfile doesn't recognize symbolic links: a ZIP entry that records one is a plain file to it, and
`extractall` writes the target's path as the file's contents. Archivey reports it as a link, with
`member.is_link` and `member.link_target`.

A field the archive doesn't record is `None`, such as `compressed_size` for a member of a
`.tar.gz`. A directory's name ends with `/` in every format, so a directory tarfile lists as `docs`
is `docs/` in archivey. `reader.get` returns `None` for a name that isn't there, where `getinfo` and
`getmember` raise `KeyError`.

`member.modified` is a `datetime` holding what the archive stores. A ZIP records local time with no
time zone, which is why zipfile gives a plain tuple, so `modified` has no time zone either. A TAR
records UTC, which tarfile gives as a timestamp, and `modified` is then a UTC `datetime`.
`member.modified_utc()` gives UTC for both, and treats a time with no zone as your local time unless
you pass another zone.

When an archive holds a name twice, zipfile, tarfile and archivey all list it twice, and reading by
name gives the last copy. Going through the member list, `member.is_current` tells you which copy
that is ([Members with the same name](opening.md#members-with-the-same-name)).

## Reading members

`reader.read` and `reader.open` work like their zipfile and tarfile counterparts, in every format
archivey opens. For ZIP, the `recommended` extra adds compression methods zipfile can't read:
Deflate64, PPMd, and zstd before Python 3.14.

Reading a directory that holds no data raises `ArchiveyUsageError`, where zipfile returns empty
bytes and tarfile's `extractfile` returns `None`. Reading a symbolic link gives its target's
contents, as tarfile does, and raises `LinkTargetNotFoundError` when the target isn't in the
archive, where tarfile raises `KeyError`. zipfile gives the link's own text, the target's path.

In a `.tar.gz`, members are compressed together, so reading one in the middle means decompressing
everything before it, with tarfile or with archivey. `stream_members()` reads each member once, in
order, in every format, which also suits 7z and RAR archives that compress members together ([Solid
archives](reading.md#solid-archives)). With `streaming=True`, a read out of order raises instead of
quietly costing time.

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
files, usually 644. The data filter is tarfile's default from Python 3.14. Before that, tarfile with
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
