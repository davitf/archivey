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
cheapest way to read, though — [Reading members](reading-members.md#two-ways-to-read)
covers when to make one forward pass instead.

If your source is a pipe or another non-seekable stream, pass `streaming=True` for a
forward-only single pass. Without it the open fails immediately rather than halfway
through — see [What you can open](#what-you-can-open) for which formats can be read
this way at all.

### `open_archive` or `open_stream`?

A `.gz`, `.bz2`, `.xz`, `.zst` and so on holds one compressed payload, with no
archive structure around it. Either entry point handles that; they differ in what you
get back:

```python
archivey.open_archive("logs.tar.gz")   # an archive: the files inside the tar
archivey.open_stream("access.log.gz")  # a stream: the decompressed bytes
```

`open_archive` works on a plain `.gz` too — you get an archive with exactly one
member, named after the file. Use `open_stream` when you just want the bytes and
know there is no tar inside.

## What you can open

| Source | What happens |
|---|---|
| A path to a file | Detected and opened |
| A path to a directory | Opens as a pseudo-archive, one member per file |
| An open binary stream | Any format if it is seekable; only some formats if not — see below |
| A sequence of paths or streams | The volumes of one multi-volume archive — see below |

Passing a `format=` that says anything other than a directory, for a path that is one,
raises `ArchiveyUsageError` rather than quietly reading the directory tree instead.

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
| `backup.rar` / `backup.exe` / `backup.sfx` + `backup.r00`, `.r01`, … | The `.rar`, the SFX stub, or any `.rNN` |

A 7z set is checked for completeness, so a missing middle part is an error rather
than a silent short read. The stub executable beside an SFX numbered set is not
concatenated into the volumes; opening it follows the first volume
(`backup.exe.001`, `backup.7z.001`, or `backup.zip.001`) so the same path works
for Linux 7-Zip and Windows 7-Zip, including when you pass `format=` after
`detect_format`. A file that *is* a self-extracting archive
(magic behind the stub) still opens as that archive, even if numbered parts sit
beside it.
The old RAR scheme needs a first volume either way: `<base>.rar`, or an SFX
`<base>.exe` / `<base>.sfx` beside the `.rNN` files. A `.rNN` on its own is read
as a lone file rather than as part of a set. A lone numbered part
(`.7z.001` / `.zip.001` / `.exe.001` with no siblings) is an incomplete set,
not a silent mis-parse. A part that does not exist at all raises
`FileNotFoundError`, as any missing path does.

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
around it, and which parts those are decides what is checked. Beside `.rNN` parts it
is their volume 1 and must share their stem, so `[alpha.rar, alpha.r00]` joins and
`[beta.rar, alpha.r00]` raises. Beside a `.partN` set it has no role at all, that
scheme spelling its own volume 1 `movie.part1.rar`, so `[movie.part1.rar,
movie.part2.rar, readme.rar]` raises. Beside a numbered set only the stub executable
7-Zip writes there makes sense, which has no part number and need not share their
name, so an `.exe` or `.sfx` is let through — a `.rar` in the same position is not.

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
archive. There is one wrinkle worth knowing: telling a `.tar.zst` from a plain `.zst`
means decompressing a little of it to look for the tar header, so when that
compressor's package is not installed the check cannot run and the bare compressor is
reported instead. You are not left guessing: opening the file raises
`UnsupportedFormatError`, naming the package to install.
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

A TAR stores member names as bytes. Archivey decodes them as UTF-8 unless you pass
`encoding=`, whatever the process locale, so the same archive lists the same way on
every machine. A PAX `path` record is decoded as UTF-8 first; only when its bytes are
not valid UTF-8 does the `encoding=` you passed apply, and without one they are escaped
as described below.

On a host whose filesystem encoding is not UTF-8, this also changes what extraction
writes. A name is written in the filesystem encoding rather than as the bytes stored in
the archive, and a name that encoding cannot represent is rejected by the extraction
guard (`PathTraversalError`, "Member name cannot be encoded for the filesystem").
Passing the locale's encoding as `encoding=` makes each name encode back to its stored
bytes on disk.

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
  `open_archive(path, encoding="latin-1")` gives `'café.txt'`. Only ZIP and TAR read
  `encoding=`; the other formats decode names their own way, and passing it to them
  emits `ENCODING_ARGUMENT_UNUSED`.

A ZIP name without the UTF-8 flag is decoded as UTF-8 when its bytes are valid UTF-8,
and otherwise with `ArchiveyConfig.zip_unflagged_fallback_encoding` (see
[ZIP](formats.md#zip)). The default fallback, `cp437`, decodes every byte, so such a
name is not escaped, though it can come back as the wrong characters. If you set the
fallback to another encoding, bytes it cannot decode are escaped in the same way.

On extraction, `STRICT` (the default) and `STANDARD` write each escaped byte
percent-encoded, as `caf%E9.txt`; only `TRUSTED` writes the stored bytes.
`ExtractionResult.presented_name` holds the name before the rewrite, which also tells
a rewritten `%E9` apart from one that was stored that way.

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
