# Opening and listing

Open an archive — unlocking it first if it needs a password — and find out what is
inside. Reading the bytes out is [Reading members](reading-members.md).

## Open and list

```python
import archivey

with archivey.open_archive("photos.zip") as reader:
    for member in reader:                    # archive order
        print(member.name, member.size, member.type)

    members = reader.members()               # the full list, or an error
    info = reader.get("subdir/a.txt")        # by name
    print(reader.format, reader.cost)
```

**By default you can open any member you like, in any order.** That is what most
callers want, and it is what the example above relies on. It is not always the
cheapest way to read, though. When you only need to read some or all of the files once,
in any order, open with `streaming=True`; [Which options to set](#which-options-to-set)
says why.

If your source is a pipe or another non-seekable stream, `streaming=True` is required.
Without it the open fails immediately rather than halfway through. See
[What you can open](#what-you-can-open) for which formats can be read this way at all.

### `open_archive` or `open_stream`?

A `.gz`, `.bz2`, `.xz`, `.zst` and so on holds one compressed payload, with no
archive structure around it. Either entry point handles that; they differ in what you
get back:

```python
archivey.open_archive("logs.tar.gz")   # an archive: the files inside the tar
archivey.open_stream("access.log.gz")  # a stream: the decompressed bytes
```

`open_archive` works on a plain `.gz` too — you get an archive with exactly one
member, named after the file. Use `open_stream` when you just want the decompressed
bytes. On a `.tar.gz`, `.tar.xz` and the other compressed tars, `open_stream` removes
the compression only and gives you the tar bytes, as `gzip.open` does, whether it
detects the format or you pass `format=ArchiveFormat.TAR_GZ`. To read the files inside,
use `open_archive`. `open_stream` refuses a ZIP, 7z, RAR, ISO or plain
`.tar`, because there is no compression layer around them to remove.

## Which options to set

The two openers take these keyword arguments:

```python
archivey.open_archive(source, *, format=None, streaming=False,
                      seekable_members=False, concurrent_members=False,
                      password=None, encoding=None, config=None)
archivey.open_stream(source, *, format=None, seekable=False, config=None)
```

The table below covers the three access options of `open_archive`: `streaming`,
`seekable_members` and `concurrent_members`. `open_stream` has only one of them,
`seekable`, which works like `seekable_members` for the one stream it returns.

Most programs need no option at all, or exactly one. The options exist so that you
never do something expensive without knowing it: without them, a seek, a second open
stream or random access on a pipe raises instead of quietly costing time. Before you set
one, look at what it costs in the table, and at whether a cheaper way of reading would
do the job.

| What you need | Open with | Limitations |
|---|---|---|
| Read or extract some or all of the members once, and the order does not matter: hash them, index them, load the data once | `streaming=True`, then `stream_members()` or `extract_all()` (`for member in reader` walks the members without their data) | No random access: `members()`, `get()`, `open()` and `read()` raise. You get one pass, even if you `break` out of it early. You do not get the full member list before the pass starts: each member is known only when the pass reaches it. `scan_members()` and `members_report()` still list the archive, but they use up the pass to do it. [More below](#streaming-for-one-pass) |
| Read one member, or a few, by name; list the archive and then read from it | Nothing (the defaults) | One member stream open at a time. On a solid archive, opening members out of archive order can decode the same block again ([details](access-and-cost.md#solid-archives-prefer-one-forward-pass)) |
| Call `seek()` on a member stream, or pass it to a library that seeks (a nested ZIP, a Parquet file, an image decoder) | `seekable_members=True` | Some extra work as you read, which depends on the codec: the stream may read the format's own index (xz, lzip), keep track of points it can seek back to, or hand a gzip or bzip2 member of 16 MiB compressed or more to the `[seekable]` accelerator when it is installed. A seek backwards may decompress the member again from its start; how far back it has to go depends on those same mechanisms, and the `[seekable]` extra only helps a large gzip or bzip2 member unless you force it on. Any seek that moves the position gives up the check of the member's stored checksum, until a seek back to the start re-arms it. If you will seek a lot, extract the member to a file first. [Details](access-and-cost.md#seeking-inside-compressed-members) |
| Several member streams open at once, for example a thread pool that reads different members | `concurrent_members=True`; call `members()` once before you fan out | A second overlapping `open()` no longer raises, so the check that catches an accidental overlap is gone. Reads from several members at once can make the reader seek back and forth in the archive, and decompress data again: on a solid archive, each stream decodes its block from the start. Reads are correct but not always faster: on formats that share one file handle, each read takes a lock, and workers can wait on it. Opening the archive several times, one reader per worker without this option, can be cheaper; it can also cost more, because each reader parses the archive's index again. Cannot be combined with `streaming=True`. [Details](access-and-cost.md#concurrent-member-streams) |
| Read from a pipe, a socket or an HTTP response | `streaming=True` | The same as the first row. Only TAR and the single-file compressors can be read this way; see [below](#what-you-can-open) |

`seekable_members` and `concurrent_members` combine freely with each other. To extract a
whole archive with safe defaults, call `reader.extract_all(dest)`
([Extracting](extracting.md)).

### Streaming for one pass

`streaming=True` is not only for pipes. On a file it tells archivey that you will read
the archive once, from start to end, and never go back. In return, a slow access pattern
fails instead of running slowly: a random `open()` raises `ArchiveyUsageError`, so an
out-of-order read on a solid archive cannot slip in and decode a block again. And no
member stream can seek, so every member you read to its end gets its stored checksum
checked, where the format stores one (ZIP, 7z, RAR). A TAR member has no checksum of its
own. A compressed TAR, or a single-file stream such as a `.gz` or `.xz`, has its codec's
checksum checked when the pass reaches the end, where the codec carries one. For `.lz4`
and `.zst` the checksum is the writer's choice, and legacy LZ4, Brotli, `.Z` and `.lzma`
carry none ([details](formats.md#single-file-compressors)).
[Details](access-and-cost.md#streaming-mode-is-one-pass)

Its other limitations:

- **Listing limits on the pass.** On a streaming reader, `stream_members()`,
  `for member in reader` and `extract_all()` are deliberately outside `ListingLimits`.
  `scan_members()` and `members_report()` enforce the limits as `members()` does, and
  7z, RAR and ISO check `max_members` when the archive is opened. See
  [Limits](extracting.md#limits).
- **A weaker TAR end check.** A corrupt header in the last block of a TAR is reported
  as a missing end-of-archive marker, not as corruption
  ([TAR](formats.md#tar-and-compressed-tar)).
- **Hard links after a filter.** On a TAR or a directory, when an `extract_all()`
  filter leaves out the first name of a hard link, extracting a later name fails,
  because the data has already gone past.

## What you can open

| Source | What happens |
|---|---|
| A path to a file | Detected and opened |
| A path to a directory | Opens as a pseudo-archive, one member per file |
| An open binary stream | Any format if it is seekable; only some formats if not — see below |
| A sequence of paths or streams | The volumes of one multi-volume archive — see below |

Passing a `format=` that says anything other than a directory, for a path that is one,
raises `ArchiveyUsageError` rather than quietly reading the directory tree instead. The
reverse, `format=ArchiveFormat.DIRECTORY` for a file or a stream, raises
`ArchiveyUsageError` too. A path that cannot be reached at all raises the operating
system's own error, such as `FileNotFoundError`, whatever `format=` says.

**A seekable stream is read from wherever it currently is**, through to the end.
Archivey treats the current position as byte 0 of the archive, so an archive stored
at a known offset inside a larger file opens without copying it out: seek to its
first byte and hand the stream over. There is no matching end bound, so this works
when the archive runs to the end of the stream; if something follows it, wrap the
stream in your own bounded view first.

**A non-seekable stream** — a pipe, a socket, an HTTP response body — needs
`streaming=True`, and works for TAR (including compressed tar) and the single-file
compressors. ZIP, 7z, RAR and ISO keep their index at the end of the archive or
address it by offset, so they have to seek: opening one from a pipe raises
`StreamNotSeekableError`, and the fix is to buffer it to a file or a `BytesIO` first.

**You do not have to find that out by trying.** `format_availability(fmt).required_source`
is the weakest source shape the format can be read from, so "pipe it if you can,
otherwise spool it to disk" is a comparison rather than a `try`/`except`:

```python
from archivey import StreamCapability, detect_format, format_availability

if format_availability(detect_format(head)).required_source <= StreamCapability.FORWARD_ONLY:
    ...  # feed the pipe straight in with streaming=True
else:
    ...  # spool to a file first
```

`StreamCapability` is ordered (`FORWARD_ONLY < SEEKABLE`), which is why `<=` reads as
"this source is strong enough" — and why the same comparison works against an already
open archive's `reader.cost.stream_capability`.

### Multi-volume archives

Only 7z and RAR split across volumes. **Pass the path of any one volume and Archivey
finds the rest**, in the naming schemes those tools produce:

| Scheme | Give it |
|---|---|
| `backup.7z.001` / `backup.exe.001` / `backup.zip.001`, `.002`, … | Any numbered part, or the stub `backup.exe` |
| `backup.part1.rar` / `backup.part1.sfx`, `.part2.rar`, … | Any part |
| `backup.rar` / `backup.exe` / `backup.sfx` + `backup.r00`, `.r01`, … `.r99`, `.s00`, … | The `.rar`, the SFX stub, or any later volume |

A 7z set is checked for completeness, so a missing middle part is an error rather
than a silent short read. The stub executable beside an SFX numbered set is not
concatenated into the volumes; opening it follows the first volume
(`backup.exe.001`, `backup.7z.001`, or `backup.zip.001`) so the same path works
for Linux 7-Zip and Windows 7-Zip, including when you pass `format=` after
`detect_format`. A file that *is* a self-extracting archive
(magic behind the stub) still opens as that archive, even if numbered parts sit
beside it.
The old RAR scheme needs a first volume either way: `<base>.rar`, or an SFX
`<base>.exe` / `<base>.sfx` beside the later volumes. The later volumes are found by
name from there, the way unrar finds them (`.r99` is followed by `.s00`, `.z99` by
`.{00`). Past a missing name, and when the first volume itself is missing, the other
old-scheme names beside it that are RAR files are part of the set too, numbered by
their names; a RAR set with a volume missing lists what the volumes present hold and
then raises `TruncatedError`. Names match in any
letter case, the first volume's included, so `ARCHIVE.RAR` + `ARCHIVE.R00` is a set
on Linux too. One exception: opened from an SFX first volume, the set is found only
when the later volumes spell the base the way the `.exe` / `.sfx` does, so
`archive.exe` beside `ARCHIVE.R00` is found from `ARCHIVE.R00` and not from the stub.
For the `.partN.rar` scheme, every part present is in the set, whatever is missing. A lone numbered part (`.7z.001` /
`.zip.001` / `.exe.001` with no siblings) is an incomplete set, not a silent
mis-parse. A part that does not exist at all raises `FileNotFoundError`, as any
missing path does.

You can also pass the volumes yourself, as an ordered sequence of paths or open
streams — useful when they are not siblings on disk, or not on disk at all. Do that
and the order you give is the order used, with no discovery. A one-item sequence is
treated as a single source, and a multi-volume sequence for any format other than 7z
or RAR raises.

Because there is no discovery, the sequence is checked for one thing discovery would
have guaranteed: that it names the parts of one archive. Concatenating
`alpha.zip.001` with `beta.zip.002` would hand you bytes that are neither archive,
and their numbering — a perfectly good `1, 2` — cannot tell you so; that raises
`ArchiveyUsageError` naming both.

All three schemes in the table above are checked. Parts of one scheme must share a
base, so `alpha.part1.rar` with `beta.part2.rar` raises, and `alpha.rar` with
`beta.r00` does too. A sequence that names parts in *two* schemes is two archives by
construction — the parts of one set are all named the same way — so
`[alpha.zip.001, beta.part1.rar]` raises as well. One name reads both ways and is
settled by the sequence rather than by itself: `Show.part1.rar` beside
`Show.part1.r00` is that set's volume 1, so `[Show.part1.rar, Show.part1.r00,
Show.part1.r01]` joins, while `[Show.part1.rar, Show.part2.rar, Show.part1.r00]`
raises.

And a name that carries no part number but is shaped like a first volume
(`backup.rar`, `backup.exe`, `backup.sfx`) only belongs beside the marked parts
around it, and which parts those are decides what is checked. Beside old-scheme parts
(`.r00` … `.r99`, `.s00` …) it is their volume 1 and must share their stem, so
`[alpha.rar, alpha.r00]` joins and `[beta.rar, alpha.r00]` raises. Beside a `.partN`
set it has no role at all, that scheme spelling its own volume 1 `movie.part1.rar`, so
`[movie.part1.rar, movie.part2.rar, readme.rar]` raises. Beside a numbered set only the
stub executable 7-Zip writes there makes sense, which has no part number and need not
share their name, so an `.exe` or `.sfx` is let through — a `.rar` in the same position
is not.

When *no* name in the sequence carries a part number, nothing in it says any of them
is a volume and none of this applies: `[alpha.rar, beta.rar]` joins, giving you bytes
that are neither archive. Refusing it would mean refusing a single-volume RAR passed
as a one-element list, which this path is documented for. Pass the parts of one set,
or let `open_archive` discover them from any one part.

The comparison ignores case, and it is only on the name, so parts of one set living
in different directories are fine. If a part has been renamed out of every pattern
(`backup.7z (1).002`, say), it is not recognised as a part at all: it is passed
through in the position you gave it, and the completeness check — which only the
numbered scheme has, RAR volumes carrying their own order in their headers — is
skipped for the whole sequence, so the order is yours to get right. Passing any part
as an open stream skips the check entirely, since a stream has no name to compare.

The old-scheme pattern is broad, because the walk past `.r99` can reach any letter:
any extension of one non-digit and two digits counts as an old-scheme part. So an
unrelated file of that shape is not passed through. `[alpha.rar, alpha.r00,
readme.p12]` raises as two sets, and so does `[alpha.zip.001, alpha.zip.002,
notes.e01]`, where `[alpha.rar, alpha.r00, readme.bak]` joins. Leave such files out
of the sequence.

## Detection

Most callers never need this: `open_archive` detects the format itself. Use
`detect_format` when you want to know what a file is *before* deciding to open it.

```python
info = archivey.detect_format("mystery.bin")
print(info.format, info.confidence)
```

Already have a reader? `reader.format_info` is the same answer from the detection
`open_archive` ran, so it costs nothing extra. It is `None` when you passed `format=`,
since no detection ran.

**Content wins over filename.** Archivey looks at the bytes first and falls back to
the extension only when they are inconclusive. When the two disagree it uses the
bytes and tells you, via a `FORMAT_EXTENSION_CONFLICT`
[diagnostic](errors-and-diagnostics.md) naming both candidates — a `.jpg` that is
really a ZIP opens fine, and so does a `.cbr` that is a ZIP (the usual comic
mislabel). You can still find out that the name lied.

`detect_format` reports the same format `open_archive` would use; a directory path
reports `ArchiveFormat.DIRECTORY`, since `open_archive` reads a directory as an
archive. A path to any part of a split set is detected on the whole set, so
`backup.7z.002` reports 7-Zip. For a numbered split set (`.7z.NNN`, `.zip.NNN` or
`.exe.NNN`), when a part before the last one is missing, part 1 included, it raises
the same `TruncatedError` that opening does. The RAR schemes are not checked this way,
as described under [Multi-volume archives](#multi-volume-archives). A lone first part
with no siblings is where the two differ: `detect_format` names the format, while
`open_archive` refuses the incomplete set. There is one more wrinkle worth knowing:
telling a `.tar.zst` from a plain `.zst` means decompressing a little of it to look
for the tar header, so when that compressor's package is not installed the check
cannot run and the bare compressor is reported instead. You are not left guessing:
opening the file raises `PackageNotInstalledError`, naming the package to install.
See [Install and extras](install.md#what-each-format-needs).

## Passwords

```python
archivey.open_archive("secret.7z", password="hunter2")
archivey.open_archive("secret.zip", password=["likely", "fallback"])
```

Put the most likely password first: every wrong candidate costs work before it is
rejected, which can be expensive (especially on 7z).

Passing a password to a format that has no encryption at all — a tar, say — is
**accepted and never consulted**, and records a `PASSWORD_ARGUMENT_UNUSED` diagnostic
you can query on `reader.diagnostics`. That is deliberate. `password=` is a *keyring you are offering*, not a claim that this
archive is encrypted — "here are the twenty passwords we know, open whatever you can"
is the point of the list form — so one plain `.tar` in a batch should not stop the run.
All three forms open alike here: a string, a list, and a `PasswordProvider` callable.
Only a string or a list records the diagnostic. A provider offers a password only when
asked, and a format with no encryption never asks, so nothing was supplied.

A *wrong* password on an archive that really is encrypted still fails loudly with
`EncryptionError`, which is the case that actually costs you something.

## Damaged archives

`members()` and `scan_members()` give you the whole listing or raise — if the archive
is damaged partway through, you get an error, never a quietly shortened list.
`members_report()` is the other half of that deal: it hands back the members it did
manage to read *together with* the error that stopped it. Iterating yields members up
to the damage and then raises.

[Errors and diagnostics](errors-and-diagnostics.md#listing-a-damaged-archive) has the
recipe and what each failure means.

## Names that do not decode

A TAR stores member names as bytes. Archivey decodes them as UTF-8, whatever the
process locale, so the same archive lists the same way on every machine. Only when a
name's bytes are not valid UTF-8 does the `encoding=` you passed apply, and without one
they are escaped as described below.

On a host whose filesystem encoding is not UTF-8, this also changes what extraction
writes. A name is written in the filesystem encoding rather than as the bytes stored in
the archive, and a name that encoding cannot represent is rejected by the extraction
guard (`FilterRejectionError`, "Member name cannot be encoded for the filesystem").
Passing the locale's encoding as `encoding=` makes each name that is not valid UTF-8
encode back to its stored bytes on disk.

A name written in a legacy encoding, such as Latin-1 `caf\xe9.txt`, is not valid
UTF-8. Archivey keeps such a name rather than failing: each byte that does not decode
becomes a surrogate escape, a code point in the range U+DC80 to U+DCFF, so
`member.name` is `'caf\udce9.txt'`.

That string cannot be encoded as UTF-8, so `print(member.name)` can raise
`UnicodeEncodeError`, and so can writing it to a UTF-8 log or JSON file. Three ways to
handle it:

- **Show it.** `archivey.terminal.escape_control_chars(member.name)` renders each
  escaped byte as `\xe9`, and the result is safe to print.
- **Keep the original bytes.** `member.raw_name` holds the name as stored in the
  archive, here `b'caf\xe9.txt'`.
- **Name the encoding.** If you know which encoding the archive uses, pass it:
  `open_archive(path, encoding="latin-1")` gives `'café.txt'`. Only ZIP, TAR, ISO and
  RAR read `encoding=`; the other formats decode names their own way, and passing it to
  them emits `ENCODING_ARGUMENT_UNUSED`. A name that is valid UTF-8 ignores it; see
  the next section.

### When valid UTF-8 wins over `encoding=`

You might expect `encoding=` to decide how every name is decoded. It does not: where
the archive does not say which encoding a name uses, archivey tries UTF-8 first, in
every format, and uses your encoding only when the bytes are not valid UTF-8. Your
encoding takes the place of the format's own fallback (cp437 for ZIP, the writer's code
page for RAR 1.5-4, escapes for TAR and ISO).

| Where the name comes from | What `encoding=` does |
| --- | --- |
| ZIP, a name with the UTF-8 flag set | Ignored; the name is UTF-8 |
| ZIP, a name without the flag | Used only when the bytes are not valid UTF-8, in place of `zip_unflagged_fallback_encoding` |
| ZIP, a name without the flag that has a matching Unicode Path extra field | Ignored; the name is the field's UTF-8 copy |
| TAR, the name, link target, `uname` or `gname` in the header block | Used only when the bytes are not valid UTF-8 |
| TAR, a PAX `path` or `linkpath` record | Used only when the bytes are not valid UTF-8 |
| ISO, a Rock Ridge or plain ISO 9660 name, or a Rock Ridge link target | Used only when the bytes are not valid UTF-8; without it, see below |
| ISO, a Joliet name | Ignored; Joliet names are UTF-16 |
| RAR 1.5-4, a name stored only as 8-bit bytes, with or without the Unicode flag | Used only when the bytes are not valid UTF-8 |
| RAR5, or a RAR 1.5-4 name with a UTF-16 copy | Ignored; the name is UTF-8 or UTF-16 |

The UTF-8 flag and PAX records declare UTF-8, so for them UTF-8 wins. So does a ZIP
Unicode Path extra field, which Info-ZIP's `zip` writes: a second copy of the name in
UTF-8, with a checksum of the stored bytes that shows it still describes them. Its UTF-8
bytes are then `member.raw_name`, and the stored bytes are in
`member.extra["alternate_raw_name"]`. The other names never say which encoding they
are in. Most tools today write UTF-8, and older ones write whatever encoding the
author's system used. Trying UTF-8 first means a legacy `encoding=` fixes the old names
without turning the UTF-8 names in the same archive, or in the next archive you open
with the same code, into mojibake. When a ZIP, TAR or RAR 1.5-4 name takes the UTF-8
reading where your `encoding=` would have given a different name, a
`member_name_encoding_inferred` diagnostic says so and names your encoding. This
differs from Python's `zipfile`
`metadata_encoding` and from `unzip -O`, which apply the encoding to every ZIP name
without the flag.

Comments follow the same rule. A ZIP member comment decodes as its name would (the
UTF-8 flag covers both), and the archive comment, which has no flag, decodes as a name
without one. A RAR 1.5-4 comment decodes as UTF-8 when it is valid, otherwise with your
`encoding=`, and otherwise as windows-1252. In both formats a byte the chosen encoding
does not define becomes a surrogate escape, as in a name, so a comment can raise
`UnicodeEncodeError` where a name can.

The cost is that a legacy name whose bytes happen to form valid UTF-8 is read as UTF-8.
For example, the Latin-1 name `Ã©.txt` is stored as the bytes `c3 a9 2e 74 78 74`,
which are also the UTF-8 for `é.txt`. Archivey lists it as `'é.txt'` even with
`encoding="latin-1"`. Real text rarely does this, because Latin-1 letters seldom fall
into valid UTF-8 sequences. When you need a specific decoding for every name,
`member.raw_name` holds the stored bytes in these cases, and you can decode them
yourself.

Without `encoding=`, an ISO image gets one more try before its names are escaped. Most
images with Rock Ridge also have a Joliet tree, whose names are UTF-16 and were
converted correctly when the image was written. A Rock Ridge name that is not valid
UTF-8 takes the Joliet name of the same file or directory, when archivey can match the
two and their ASCII characters agree. The member then carries a
`member_name_encoding_inferred` diagnostic. A Joliet name that was cut short (writers
cut them at 64 characters) does not agree, so that name is escaped instead. A relative
symlink target is decoded to match: each part of it that names a file or directory in
the image is spelled the way that member's name is.

A ZIP name without the UTF-8 flag is decoded as UTF-8 when its bytes are valid UTF-8,
and otherwise with your `encoding=`, or without one with
`ArchiveyConfig.zip_unflagged_fallback_encoding` (see [ZIP](formats.md#zip)). The
default fallback, `cp437`, decodes every byte, so such a name is not escaped, though it
can come back as the wrong characters. If you pass or set another encoding, bytes it
cannot decode are escaped in the same way.

On extraction, `STRICT` (the default) and `STANDARD` write each escaped byte
percent-encoded, as `caf%E9.txt`; only `TRUSTED` writes the stored bytes.
`ExtractionResult.presented_name` holds the name before the rewrite, which also tells
a rewritten `%E9` apart from one that was stored that way: after the rewrite it
differs from the written name in the escaped bytes. It is also set when an absolute
name loses its root (`/etc/x` written as `etc/x`), and then differs from the written
name only by that root. `ExtractionResult.rewrites` says which of the two happened:
`NameRewrite.PORTABLE_NAME`, `NameRewrite.REROOTED`, or both.

## Duplicate names and is_current

Appending to a tarball, or updating a 7z, can leave **the same member name in the
archive more than once**. Archivey never hides the older copies — `members()` and
iteration return every entry — but it marks which one is live:

- The **last** entry with a given name has `is_current=True`.
- Earlier entries with that name have `is_current=False`.

`reader.get(name)` and `reader.open(name)` follow the same rule: a name resolves to
the last entry, which is the live one.

`extract_all` also follows it. Superseded entries are skipped and reported as
`ExtractionStatus.SUPERSEDED` (distinct from `NOT_OVERWRITTEN`, which is about files
already on disk, and from `OVERWRITTEN`, which is a member that *was* written this run
and then had its destination taken by a later one), so what lands on disk matches a
fresh write. A streaming TAR has no index to say which entry is last. It writes the
earlier entry, then takes it back when the later one arrives, so a progress callback
sees it written and the report ends with it `SUPERSEDED`.

**Selecting members by name is the one place to be careful.** A name in a selector —
`extract_all(members=["notes.txt"])`, `stream_members(members=["notes.txt"])` —
matches *every* entry with that name, not just the live one. For `extract_all` that
is harmless, since the superseded ones are skipped anyway; `stream_members` has no
such skip and will hand you each version in turn. Pass the `ArchiveMember` itself
when you mean one specific entry — selectors match those by identity.

Names match exactly, and a directory's name ends in `/`. Select the directory `docs/` as
`members=["docs/"]`; `members=["docs"]` selects nothing, and its diagnostic names
`docs/`. `members=["docs/"]` selects the directory entry only, not the files inside it.
`reader.get()` and `reader.open()` also need the exact stored name.

A name that matches nothing is not an error, but it is not silent either. Each such entry
gets a `MEMBER_SELECTOR_UNMATCHED` diagnostic once every member has been offered to the
selector (see [Errors and diagnostics](errors-and-diagnostics.md)). With
`stream_members()` that is when you iterate to the end; a loop you leave early gets no
report, because a later member could still have matched.

In your own code, filter for the live state:

```python
with archivey.open_archive("updated.tar") as reader:
    current = [m for m in reader if m.is_current]
```

Or keep every version, for a history view:

```python
with archivey.open_archive("history.tar") as reader:
    for member in reader:
        tag = "" if member.is_current else " [superseded]"
        print(f"{member.name}{tag}")
```
