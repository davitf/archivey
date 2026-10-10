# TAR

Current maintainer truth for the TAR backend, plain and compressed. A tar file is a run of
512-byte headers, each followed by that member's data, ended by zero blocks. There is no
index, no per-member compression and no checksum over data. archivey does not parse it:
stdlib `tarfile` does, reading either the source itself or a decompressor archivey built.
Most of what is peculiar here follows from those two facts. Registers keep the status;
this page states the behaviour and links the row.

## At a glance

| | |
| --- | --- |
| Read | Yes, through stdlib `tarfile` in mode `r:` (random access) or `r\|` (streaming). archivey hands it a `fileobj=` every time and never uses tarfile's own `r:gz` / `r:bz2` / `r:xz` modes |
| Write | **Not shipped**, for any format ([writing design](../investigations/archive-writing-design.md)) |
| Source | Seekable for random access. Any source, a pipe included, with `streaming=True` |
| Listing cost | `REQUIRES_SCANNING` for a plain tar, `REQUIRES_DECOMPRESSION` for a compressed one |
| Access cost | `DIRECT` for a plain tar. `SOLID` for a compressed one, with `is_solid=True` and `solid_block_count=1` |
| Stream capability | `SEEKABLE` or `FORWARD_ONLY`, taken from the source |
| Core dependencies | None. Plain, gzip, bzip2, xz, lzma-alone, zlib, lzip and `.Z` read on a zero-dependency install |
| Optional | `[recommended]`: Zstd (`backports.zstd`, stdlib on 3.14+), LZ4 (`lz4`), Brotli (`brotli`). `[seekable]`: `rapidgzip`, a seek index for gzip and bzip2 |
| Encryption | None in the format. A password is accepted in every form and never consulted; a concrete one emits `PASSWORD_ARGUMENT_UNUSED` |
| Refuses | Random access on a non-seekable source · a start offset, so no prefixed or self-extracting tar · writing. Device, FIFO and socket members list as `OTHER` and are blocked at extraction by the shared filter |

**Four things a reader might expect and will not find.** `ArchiveInfo.member_count` is
always `None`, because nothing short of the walk knows it (§1). Listing a `.tar.gz`
decompresses the whole stream, members included, and opening a member after that seeks
backwards in the decompressor (§2.3). A plain tar has no checksum over member data, so a
flipped byte in a member comes back as a wrong byte with no error (§4). And a pre-POSIX
"v7" tar has no magic at all, so detection finds it only by its extension (§2.1).

## 1. Shape

Four properties generate most of this page.

```
[ header ][ data, padded to 512 ] [ header ][ data … ] … [ 512 zeros ][ 512 zeros ][ padding? ]
    │                                                         └────── end of archive ──────┘
    ├── name[100] mode uid gid size[12] mtime[12] chksum typeflag linkname[100]
    └── "ustar" at offset 257 · uname gname devmajor devminor prefix[155]

typeflag  0 file · 1 hardlink · 2 symlink · 3/4 device · 5 dir · 6 FIFO
          x PAX record for the next header · g global PAX record
          L / K GNU long name / long link name · S old GNU sparse
```

**There is no index; each header sits in front of its own data.** Listing is a walk from
the first header to the end, and each header's `size` field is the only way to find the
next one. Everything about cost follows. Nobody knows how many members there are until the
walk ends, so `member_count` is `None` and `members_report_if_available()` returns `None`
before a pass. A plain tar's member data sits at a fixed offset once the walk has found
it, so random opens are `DIRECT`. The walk works forward-only, so `streaming=True` works
on a pipe and hands out each member as its header goes past. A hardlink names an earlier
member, so unfiltered extraction resolves every hardlink in one pass. Appending
(`tar -r`, `tar -u`) adds a later member with the same name rather than editing the old
one, so duplicate names are ordinary and the last one is current.

**The end is two zero blocks, and tarfile does not say why it stopped.** A trailer, a
corrupt header after the first, and a source that simply ran out all end tarfile's walk
the same way, with no exception. archivey reconstructs the reason from the error the
last header parse raised before tarfile swallowed it (§2.2), and the result has three
outcomes: a header tarfile rejected is corruption; a missing, short or damaged trailer
is a warning, because a complete tar written without a trailer and a tar truncated
exactly at a member boundary are the same bytes, and a zero block followed by a damaged
one still ends a whole listing; bytes after a good trailer are trailing data. An empty tar is nothing but
zeros, so a zero-filled file of any block-aligned length is a valid empty archive
([ADR 0015](../decisions/0015-zero-filled-files-are-valid-empty-tars.md)). Two tars joined
with `cat` list as the first one plus a trailing-data diagnostic, because the first
one's trailer ends the walk.

**Compression wraps the whole archive, never a member.** A `.tar.gz` is one gzip stream
whose output is a tar. So every compressed tar is solid: listing means decompressing
every byte, including the member data between headers (`REQUIRES_DECOMPRESSION`), and a
random open is a seek in the decompressed stream (§2.3). Detection has to decompress a
prefix to know it is a tar at all (§2.1). The tar layer itself stores data as-is, which
is why `member.compression` is `(STORED,)` for a file or hardlink and `()` for everything
else. The only data integrity check is the compressor's, and a plain tar has none.

**Metadata comes in three layers, and the later ones override the earlier.** The ustar
header has fixed-width fields: a 100-byte name with a 155-byte prefix, 12-byte octal
sizes and times. GNU extensions add long names and link names (`L`, `K`), base-256
numbers for sizes and times that do not fit in octal, and sparse files. PAX adds
key-value records, per member (`x`) or for the rest of the archive (`g`), that replace
any field. tarfile merges all three before archivey sees a member, which has three
consequences here. Where a name came from decides how its bytes were decoded, and
archivey has to infer that to rebuild `raw_name` (§2.2). `extra["tar.pax_headers"]`
holds the merged records, global ones included, not the member's own block. And
access time and inode-change time exist only as PAX records, so a member without them
has neither.

## 2. The pipeline here

Each stage: who does the work, what is TAR-specific rather than general, what is refused.

### 2.1 Identify

One magic, `ustar` at offset 257, which covers both the POSIX spelling (`ustar\x0000`)
and the GNU one (`ustar  \x00`). Extensions: `.tar`, the compound forms `.tar.gz`,
`.tar.bz2`, `.tar.xz`, `.tar.zst`, `.tar.lz`, `.tar.lz4`, `.tar.lzma`, `.tar.br`,
`.tar.zz` and `.tar.Z`, the short aliases `.tgz`, `.tbz`, `.tbz2`, `.txz`, `.tzst` and
`.tlz`, and `.cbt` for comic books.

A compressed tar carries only its compressor's magic. Detection first finds the
compressed stream, then runs the **inner-TAR probe** (`_probe_inner_tar` in
`internal/detection.py`): decompress 512 bytes, from at most 1 MiB of compressed input,
and look for `ustar` at 257. A hit upgrades `GZ` to `TAR_GZ` and reports it as
`PROBABLE`, because a structural check is weaker than an exact magic. If the codec is
not installed or the probe's budget does not cover it, detection reports the bare
compressor, and a `.tar.gz` extension over a bare `GZ` result is treated as the expected
deferral rather than a mismatch.

Two things the magic cannot do. A **v7 tar**, written before POSIX added the magic
(`tar --format=v7`), has none, and without a `.tar` extension it fails with
`FormatDetectionError`. Inside a compressor it opens as the bare compressor even when
named `.tar.gz`, and its one member is the uncompressed tar. And
a **zero-filled file** named `.tar` opens as an empty archive by extension, while
`detect_format()` on the same bytes refuses it, because an empty tar has no header to
carry the magic ([`docs/gotchas.md`](../../docs/gotchas.md)).

TAR takes no part in the prefixed-archive scan. `open_read` calls `reject_start_offset`,
so a tar behind a stub is not found or opened.

### 2.2 Open and list

**archivey chooses what tarfile reads.** For a plain tar, tarfile reads the
`ArchiveSource` itself. For a compressed tar, archivey opens its own codec stream,
buffered so tarfile never sees a short read, and tarfile reads that. A path source goes
to the codec as a path, so the codec can use an accelerator and a static ratio. Either
way tarfile gets `fileobj=`, so it never owns or closes the handle, and archivey closes
the decompressor it built.

**Reads whose size the archive chooses are bounded.** In random-access mode the fileobj
is wrapped in `_BoundedTarFileobj`. tarfile reads a PAX record or a GNU long name with
one `read(size)`, where `size` is the header's field, up to 8 GiB in octal and more
through base-256. Over a decompressor the wrapper asks in steps, so the allocation
follows the bytes that exist. Over the source it passes through, because the source
already clamps a read to what is left ([`threat-model.md`](../threat-model.md) O15).
Streaming needs neither: tarfile's `_Stream` reads in `bufsize` chunks. What streaming
does need is a guard on the skip. tarfile skips an unread member by reading
`size // bufsize` chunks and does not stop when they come back empty, so the cost
followed the declared size: a 2 KiB archive declaring a 2**45-byte member looped for
hours. Before each header after the first, `_read_through_member_data` reads the rest of
the previous member's data area itself (up to `TarFile.offset`, in 64 KiB chunks) and
raises `TruncatedError` at the first short read, so the skip costs the bytes present. On
an honest archive these are the bytes tarfile would have read anyway, and its own skip
is left with nothing to do.

**How the end is classified.** `TarFile.next()` returns `None` on a zero block and on a
header it rejects after the first, and swallows the error that told them apart.
`_TarInfo.fromtarfile` records that error's class on the `_TarFile` (`stopped_on`:
`EOFHeaderError` is a zero block, `InvalidHeaderError` a rejected header) before it is
swallowed. The class is the same in both access modes and does not depend on which
bytes were read, so both modes run the same EOF check, in this order:

  1. The last header parse rejected the header (a bad checksum or number field, a
     negative size, PAX records that do not parse). The listing was cut short, so it is
     `CorruptionError` whatever the diagnostic policy and whatever follows: more
     members, nothing, or a zero block (a member whose data starts with 512 zero bytes,
     which step 2 would take for the second trailer block).
  2. Otherwise read the next block. tarfile has already consumed the first trailer block,
     so this is the second. A null block is a good trailer. A short or empty read emits
     `ARCHIVE_EOF_MARKER_MISSING` under the ordinary policy, a warning by default. A
     non-null block after a zero block, with at least one member listed, means the
     listing is whole and only the end-of-archive marker is damaged, so it is
     `ARCHIVE_EOF_MARKER_MISSING` (`expected_marker="second_zero_block"`,
     `observed_kind="nonzero"`) under the ordinary policy, as GNU tar ("A lone zero
     block") and 7-Zip list it with a warning (maintainer ruling, 2026-10-06), and step 3
     runs from the block after it. With no member before the zero block it is
     `CorruptionError`, with `expected_marker="two_zero_blocks"`.
  3. After a good trailer, or a damaged second block, scan up to 1 MiB for a non-zero
     byte and emit `ARCHIVE_TRAILING_DATA` at the first one. Zeros pass, because `tar`
     pads to 10 KiB records. On a compressed tar the tail is decompressed to look at
     it, and a tail that does not decode (a truncated footer, junk after the compressed
     stream) ends the scan with no diagnostic. A whole-stream checksum that fails there (gzip CRC-32
     or ISIZE, zlib Adler-32, zstd or lz4 content checksum, lzip CRC-32) raises
     `CorruptionError`: it covers the members already read, and with `tar -b128`
     padding (64 KiB) the scan is where it is reached. When the scan stops at 1 MiB
     with the compressed stream still going, that checksum was never checked, and
     `DIGEST_UNVERIFIABLE` (`reason="trailing_scan_limit"`) says so. An xz integrity
     check fails with liblzma's generic "Corrupt input data", and a bzip2 block CRC
     with the same "Invalid data stream" as any bad bzip2 input; a junk tail gives
     both too. So on those two a tail that does not decode is `DIGEST_UNVERIFIABLE`
     (`reason="trailing_decode_failed"`): it may be a failed check over the members.
     The same check can instead be reached while the last member is read, when the
     codec has already read to the stream's end; that read raises `CorruptionError`.
     Which one happens depends on the codec's input chunking, so on member size.

**The walk stops at the listing caps.** Random-access listing pulls headers through
`iter(TarFile)` in batches of up to 1 024 under one lock hold. A batch never asks for
more than `ListingLimits.max_members` has left plus one, and is cut short where a low
count of its header text passes `max_metadata_bytes`, so a header bomb costs about one
header past either cap and no more. That one header is bounded too: tarfile reads a PAX
`x`/`g` header or a GNU `L`/`K` name whole, so `_TarInfo._proc_member` refuses one whose
size field declares more than is left of `max_metadata_bytes` (the whole cap when the
listing is not enforcing it, and on a streaming walk) with `ResourceLimitError`, before
the read. The budget reaches it through a context variable the reader sets around each
tarfile call that parses headers. A sparse member's map counts too, 24 bytes per
entry: tarfile keeps it on the `TarInfo`, and a PAX sparse 1.0 map is data, not header
text, so a few kilobytes of compressed map can hold millions of entries. Past a cap that is not enforced (`stream_members()`
on a random-access reader) the batches go back to full size. Batching keeps the walk a
dense pass; one header per lock hold was measurably slower on ordinary listings. When
the walk fails partway through a batch, the headers already parsed are handed out first,
so `members_report()` keeps its salvaged prefix. tarfile still keeps every header it has
parsed in `TarFile.members`, so a listing holds each header twice: once as tarfile's
`TarInfo` and once as the `ArchiveMember`. On a streaming reader, `scan_members()` and
`members_report()` count members against the cap as they arrive and raise at the one
past it. `stream_members()` and forward-only iteration are not capped, by design, and
there both lists grow for the whole pass.

**Member metadata** is mapped in `_to_member`:

| Field | From |
| --- | --- |
| `type` | typeflag through tarfile's predicates: directory, symlink, hardlink, file. Everything else, including devices, FIFOs and contiguous files, is `OTHER`, with `extra["tar.type"]` holding the typeflag byte |
| `name` | tarfile's decoded name after PAX and GNU overrides, normalized with `backslash_is_separator=False`, since a backslash is a legal POSIX filename character. `./` prefixes go and a directory gets a trailing `/`, with `MEMBER_NAME_NORMALIZED` for each change |
| `raw_name` | Rebuilt by `_recover_raw_name`. A PAX `path` is UTF-8 unless its own block says `hdrcharset=BINARY`; a ustar or GNU long name is re-encoded with the archive `encoding` and tarfile's `surrogateescape`. tarfile does not record where a name came from, so a name equal to `pax_headers["path"]` is taken as PAX. `None` when no codec reproduces it |
| `link_target` | `linkname` exactly as stored, for symlinks and hardlinks. A hardlink stores an archive path, so a tar made from `./d` stores `./d/b` while the member it names is listed as `d/b`. `link_target_member` is the resolved one |
| `size` | `TarInfo.size` for a file, which for a sparse member is its logical size. `None` for everything else. `compressed_size` is never set |
| `modified` | `TarInfo.mtime`, where tarfile has already applied a PAX `mtime` with its fraction. A value `datetime` cannot hold is `None` plus `MEMBER_TIMESTAMP_INVALID`. So is a PAX `mtime` that is not a number, which tarfile reads as `0` without an error: it lists as `None`, not as the Unix epoch |
| `accessed` | PAX `atime` only. A record that is not a number, or out of range, is `None` plus `MEMBER_TIMESTAMP_INVALID`, as for `modified`; the same holds for `created` and `ctime` |
| `created` | The PAX `LIBARCHIVE.creationtime` keyword, which libarchive writes when the source OS has a birth time. No other TAR writer is known to store one, so it is `None` for most archives |
| `ctime` | PAX `ctime` only. It is the inode-change time (`st_ctime`), so it never fills `created`. A libarchive tar can carry both |
| `mode`, `uid`, `gid`, `uname`, `gname` | Straight from the header. `mode` keeps the permission and setuid/setgid/sticky bits only, masked before `stat.S_IMODE`, so a negative or wider-than-32-bit base-256 mode cannot fail the listing |
| `is_sparse` | `TarInfo.issparse()`, which is true for the old GNU `S` typeflag and for all three PAX sparse encodings |
| `extra` | `tar.type` always; `tar.pax_headers` when there are any; `tar.devmajor` / `tar.devminor` for device members |

`encoding=` reaches `tarfile.open`; without it the reader passes `"utf-8"`, not
tarfile's own default (`tarfile.ENCODING`, the filesystem encoding on POSIX), so a
listing does not depend on the process locale. tarfile's `surrogateescape` handler
stays. A ustar or GNU name, link target, `uname` or `gname` declares no encoding, so
when tarfile decoded it with the caller's `encoding=`, `_to_member` takes the stored
bytes back and uses their UTF-8 reading when they are valid UTF-8, as every format does
for an undeclared name (design rules, ruled 2026-10-07): `encoding=` changes such a field
only when its bytes are not valid UTF-8. A name taken that way, where `encoding=` would
have given a different one, emits `MEMBER_NAME_ENCODING_INFERRED` naming the caller's
codec, as ZIP and RAR 1.5-4 do. A PAX record is decoded strictly as UTF-8 first
and falls back to the archive codec (with `surrogateescape`) only when that fails, or
outright for the member's own `hdrcharset=BINARY`; so `encoding=` changes a PAX name
only when its bytes are not UTF-8. A `BINARY` record declares no encoding, so it gets
the same UTF-8 reading as a ustar name. A member block that repeats a global `BINARY`
cannot be told from one that inherits it (`_pax_field_is_utf8`), so under a non-UTF-8
`encoding=` its name keeps the codec's reading and `raw_name` is not the stored bytes;
that needs a crafted archive, and is pinned by a test. Because that fallback
is the archive codec, the UTF-8 default reaches such PAX names too: without `encoding=`
their undecodable bytes are surrogate escapes whatever the locale, and `raw_name` is the
stored bytes.

### 2.3 Member data

tarfile's `extractfile()` does the work in both modes, which is what keeps sparse
expansion correct without a second implementation. What differs is what sits under it.

**A sparse map is checked against the member's data area first.** tarfile reads the
map's chunks one after another from the start of the data area and does not know how
much the member stores, so a map claiming more reads the next header and member as this
member's content. The reader records where each member's data area ends as tarfile
parses the header, and refuses a map whose chunks add up to more. The end is known only
in whole blocks, so up to 511 bytes of the member's own padding can still read as data.
The same bound runs the other way: a map that accounts for more than 511 bytes fewer
than the member stores is refused too, because tarfile never serves the bytes no chunk
names, and leftover bytes inside a member are damage (DR-3); GNU tar 1.35 extracts such
a member without them. A negative entry, a chunk that ends past the logical size (even
an empty one) and a logical size past 2**63 - 1 (no file's size) are refused as well.
tarfile drops the stored bytes of a non-empty chunk past the logical size (DR-3). An
empty entry past it loses no bytes in tarfile; it is refused because the map contradicts
its own declared size, and because the readers disagree on the extracted length (DR-1).
GNU tar 1.35 refuses a chunk past the logical size in old GNU and PAX 1.0, and reads it
in PAX 0.0 and 0.1, where it extracts a longer file than tarfile serves. Each of these is
`CorruptionError` when the member is opened (streaming: on its first read, so a
consumer that skips it is unaffected).

**An out-of-order or overlapping map is `UnsupportedFeatureError`.** GNU tar 1.35 reads
both, writing each chunk at the offset the map gives. tarfile stitches the chunks into
one run instead, which is a wrong answer (DR-1), and serving them in logical order on
the streaming path would mean buffering up to the member's logical size (DR-9). The map
is valid data archivey cannot serve, so it is unsupported, not corrupt (DR-4); a map
that is also damaged raises `CorruptionError`. Only crafted archives are known to hold
such a map, and there is no plan to support it; revisit if a real archive appears (§6). Empty entries are exempt from the order
check, since GNU tar ends a map with `(realsize, 0)` and the old GNU header pads its
slots with `(0, 0)`. One function makes all of these checks for the four encodings (old
GNU and PAX 0.0, 0.1, 1.0), since tarfile turns each into the same list of
`(offset, numbytes)` pairs.

**A plain tar reads the member's bytes from the source**, at the offset the walk found.
Random opens cost one seek each, and members can be read in any order.

**A compressed tar seeks in the decompressed stream.** Forward is decode-and-discard.
Backward goes to the nearest resume point before the target and decodes forward from
there: the start of the stream for plain gzip, bzip2 and zlib, a `rapidgzip` index point
when that accelerator is engaged, and a block or member boundary for xz and lzip. Each
backward seek that discards 1 MiB or more reports `STREAM_REWIND_REDECOMPRESSES` with
the byte count; the first on a stream logs, later ones only escalate, and a caller who
wants a rewind to fail sets that code to `RAISE`. A listing ends at the far end of the
stream, so the first `open()` after `members()` is already a backward seek.
`stream_members()` decodes the stream once and is the path `docs/formats.md` tells
callers to use. On a random-access reader whose walk has not ended it is one pass
(`_iter_with_data_random_access`): the walk parses one header at a time, registers the
member as it arrives (so `members()` afterwards serves the same list), and the member's
data is read from where its header left the stream, before the next header. After a full
listing it reads the members' data in archive order, one forward sweep after a single
seek back to the first member. `extract_all()` takes the one-pass path too (§2.4).

**`streaming=True` hands each member out as the pass reaches it.** A member's stream is
good only until the pass moves on, because tarfile reads the next header from the same
position; keeping one and reading it later raises `ValueError` on a closed file. A
hardlink or symlink in a streaming pass resolves against members already seen.

**A hardlink resolves to an earlier member only, in both modes.** A hardlink is a
reference to a file already archived: tarfile's `_find_link_target` searches only the
members before the link and takes the last match, and `tar(1)` links to what it has
already written. The base reader never looks forward for a hardlink's target, for any
format, so a hardlink whose only same-named member comes after it has no
`link_target_member`, and opening or extracting it raises `LinkTargetNotFoundError`, in
random access as in a streaming pass. A symlink is a path, not a reference, and resolves
to the last member of that name either way.

**`MemberStreams.CONCURRENT`** puts one lock around every operation that touches
tarfile's shared handle: the walk, `extractfile`, each member read, seek and close, the
EOF checks and archive close. The lock makes interleaved reads correct. It does not make
them parallel, and on a compressed tar they still share one decompressor. A streaming
reader takes the same lock, uncontended, so both modes have one critical-section shape.

**Nothing verifies member data.** The format has a checksum over each header and none
over data. On a compressed tar the compressor's own check catches a corrupted byte when
decoding reaches the end of the stream; gzip's CRC32 and xz's block checks are what a
tar reader relies on. A plain tar has no such check.

### 2.4 Extract

Path safety, collisions, limits and the extraction filter are the shared machinery
([`safe-extraction`](../../openspec/specs/safe-extraction/spec.md)). Three things are
TAR's own.

**Hardlinks are resolved by a pull-based coordinator.** The source always comes first in
a tar (a hardlink with no earlier source has no target, §2.3), so an unfiltered
`extract_all()` links every hardlink in one pass with `os.link()`. A `members` selector or `filter` can select a link and exclude its source.
Then a seekable reader makes one second pass for all such links together, and a
forward-only one records each as a failure under `OnError`. A cross-device link falls
back to copying from a path already written, as does a link past the filesystem's
link-count limit (1024 names for one file on NTFS). The full matrix is in
[`format-tar`](../../openspec/specs/format-tar/spec.md).

**A hardlink gets what its source gets.** The `linkname` is a member name, never a path:
extraction links to the file the end of the link chain was written to, and does not
check the string (maintainer decision, 2026-10-07). A link whose source the policy
refuses (`../x`, or `/x` under `STRICT`) is refused with it, selected or not, so the
second pass never writes a refused member's bytes under the link's name. A middle link
refused for its own name does not refuse the links after it, and a hardlink to a
symlink is written as that symlink. A hardlink to a symlink with an empty `linkname`
fails as a link to a non-file, refused symlink or not. A filter's change to a link's
`link_target` does nothing. `tests/test_hardlink_target_rule.py` has the matrix.

**Special files are blocked.** A device, FIFO or socket member is `OTHER`, and the
default filter records it as `BLOCKED` with `FilterRejectionError` rather than creating it.

**A sparse member is written dense and its holes count as output.** Extraction copies the
member's logical bytes, so every hole becomes zeros on disk and in the decompression-ratio
count. Counting the holes is a decision (§6): written out, they fill the disk like any
other output. The consequence is in §5.

Where the ratio check draws its numbers from depends on the source. A compressed tar
opened from a path is checked against the file's size; one read from a stream is checked
live against compressed bytes consumed, which is why a piped `.tar.gz` bomb stops
mid-pass. A plain tar has nothing to decompress and is checked against the archive size.

**`extract_all` is one forward pass in both modes.** Random access does not list the
archive first: `TarReader._extraction_listing` has the pass enforce `ListingLimits` as
members arrive (`ResourceLimitError` at the member that crosses a cap, before it is
written), so a `.tar.gz` is decoded once and no rewind is reported. What that gives up is
knowledge of the future, the same as a streaming pass: a corrupt header raises after the
members before it are written (no longer fails closed), and the duplicate-name cases
`safe-extraction` lists as differing in a streaming pass differ here too. A hardlink
source is always earlier (§2.3), so every source is written or excluded by the time its
link arrives; one a selector excluded is read again in the orphan second pass.

### 2.5 Write

Not shipped, for any format. Tests build fixtures with stdlib `tarfile` and, where the
shape matters, with GNU `tar` (§8).

## 3. In the wild

**GNU tar writes `--format=gnu` by default.** Measured on GNU tar 1.35 here
(`tar --show-defaults` prints `--format=gnu -b20`): long names go in `L` records, a
sparse file under `--sparse` gets the old `S` typeflag, and every archive is padded to a
10 240-byte record. Under `--format=pax` or `--format=posix`, which the GNU manual names as
its future default, the same sparse file is a plain `0` member plus `GNU.sparse.*` PAX
records. Measured: `truncate -s 10M f; tar cf x.tar --format=pax --sparse f` is 10 240
bytes.

**Hardlink targets keep the path as given.** `tar cf x.tar ./d` stores member names and
hardlink targets with the `./` prefix. archivey normalizes the name and leaves
`link_target` alone (§2.2).

**Writers disagree on the size of an empty archive.** Python's `tarfile` writes 10 240
zero bytes, Go's `archive/tar` 1 024, and GNU `tar -b N` any multiple of 512 × N. That
disagreement is why a length rule cannot tell an empty tar from a zero-filled file
([ADR 0015](../decisions/0015-zero-filled-files-are-valid-empty-tars.md)).

**Concatenated tars are common and mostly accidental.** `cat a.tar b.tar` produces one
file with a trailer in the middle (`tar -A` does not: it overwrites the first trailer). GNU `tar -i` reads past it;
archivey lists the first archive and reports `ARCHIVE_TRAILING_DATA`.

**An ISO 9660 image opened as TAR lists empty.** Its first 32 KiB are zeros, which reads
as an empty tar, and the first non-zero byte after them is reported as trailing data.

## 4. Threat surface

TAR-specific only. Path traversal, absolute names and link escapes are the shared
extraction checks (§2.4).

- **Header fields size reads.** A PAX record or GNU long name is read in one call sized by
  the header. Bounded where the read reaches bytes, so a 10 KiB archive cannot allocate
  6 GiB ([`threat-model.md`](../threat-model.md) O15).
- **Every header is a member to keep.** Headers compress to a few bytes each: 300 000
  empty headers gzip to 1.8 MB. The walk stops about one header past either listing cap (§2.2).
  `stream_members()` and forward-only iteration are not capped, and hold every header
  until the pass ends (O1).
- **A sparse member is a ratio claim.** A few hundred bytes of sparse map can declare a
  logical size of terabytes. Extraction writes the logical bytes, so what stops it is the
  decompression-ratio guard, the same one that stops a gzip bomb; `ExtractionLimits`
  with `max_ratio=None` removes it.
- **Nothing authenticates data.** Only the header has a checksum, and it is a byte sum
  over 512 bytes, not a digest. A plain tar with altered member data lists and reads
  cleanly. Anything that needs integrity has to come from the compressor or from outside
  the archive.
- **A listing can end early without an error.** tarfile treats a corrupt header after the
  first as the end. archivey turns that into `CorruptionError` in both access modes
  (§2.2). What stays a warning is a missing trailer. A caller who needs a provably complete
  listing sets `ARCHIVE_EOF_MARKER_MISSING` to `RAISE`, as `DiagnosticPolicy.strict()`
  does.
- **Link targets are header text.** A symlink or hardlink target costs no decode to read,
  so resolving links at listing is free here, unlike 7z ([`7z.md`](7z.md) §2.2). It is
  still attacker text and is counted against `max_metadata_bytes`.

## 5. Sharp edges

*Where it lives*: **format**, inherent and no implementation fixes it · **library**, stdlib
`tarfile`'s behaviour, fixable only upstream or by replacing it · **archivey**, ours.

| What you see | Where it lives | More |
| --- | --- | --- |
| `member_count` is `None`, even after listing | **format** | No index (§1). `len(reader.members())` after the walk is the count |
| Listing a `.tar.gz` takes as long as extracting it | **format** | Headers are spread through the compressed stream, so finding them decodes everything (§1) |
| Reading members of a `.tar.gz` by name is slow, and reports `STREAM_REWIND_REDECOMPRESSES` | **format** / **archivey** | Each backward seek decodes from the nearest resume point (§2.3). `stream_members()` decodes once. `[seekable]` adds resume points for gzip and bzip2 |
| A seek past the end of a member returns the member size, not the target | **library** | stdlib `ExFileObject` clamps the position; reads agree either way ([`known-issues.md`](../known-issues.md)) |
| A tar with no trailer warns `ARCHIVE_EOF_MARKER_MISSING` and still lists | **format** | Complete-without-trailer and truncated-at-a-boundary are the same bytes. Set the code to `RAISE` when completeness matters |
| Two tars joined with `cat` list as one archive's members plus `ARCHIVE_TRAILING_DATA` | **format** / **archivey** | The first trailer ends the walk. archivey does not read past it the way `tar -i` does (§6) |
| A byte more than 1 MiB past the trailer goes unreported | **archivey** | The trailing-data scan is an effort bound, not a guarantee (§2.2). On a compressed tar that also leaves the stream checksum unchecked, reported as `DIGEST_UNVERIFIABLE` |
| A `.tar` of nothing but zeros opens as an empty archive | **format** | That is what an empty tar is ([ADR 0015](../decisions/0015-zero-filled-files-are-valid-empty-tars.md)). `detect_format()` still refuses it |
| A v7 tar with no extension is not detected | **format** | No magic to find (§2.1). Pass `format=ArchiveFormat.TAR` |
| A hardlink's `link_target` is `./d/b` while the member it names is `d/b` | **format** / **archivey** | `link_target` is documented as stored text. Use `link_target_member` |
| A hardlink placed before the only member it names does not extract | **format** | A hardlink refers to an earlier member, as tarfile and `tar(1)` read it; `LinkTargetNotFoundError` in both modes (§2.3) |
| Extracting a sparse file refuses with a ratio error, or fills the disk with zeros | **archivey** | Holes are written as zeros and counted as output (§2.4). Measured: a 10 MiB sparse file with one byte of data is a 10 240-byte tar, and `extract_all()` refuses it at 1024:1. By design (§6); raise `max_ratio` for an archive known to hold sparse files |
| GNU tar extracts a sparse file and archivey refuses it with `UnsupportedFeatureError` | **archivey** | The sparse map is out of order or overlapping. tarfile stitches such chunks into one run, a wrong answer, so archivey refuses the map (§2.3). Only crafted archives are known to hold one. By design (§6); there is no plan to support it, and it is revisited if a real archive appears |
| A member's data changed and nothing noticed | **format** | No data checksum in a plain tar (§4) |
| A streaming pass over millions of members uses memory in proportion | **library** / **archivey** | tarfile appends every header to `TarFile.members`, and the pass keeps its own list for `scan_members()` |
| `encoding=` has no effect on some names | **archivey** | PAX names are UTF-8 by definition, and a ustar or GNU name whose bytes are valid UTF-8 is read as UTF-8 too; `encoding=` decodes only bytes that are not valid UTF-8 (§2.2) |

## 6. Decisions

| Choice | Why | Rejected |
| --- | --- | --- |
| Read through stdlib `tarfile` | Zero dependencies, and it already handles GNU long names, base-256 numbers, PAX, globals and every sparse encoding | A native header walker now. It is the planned structural fix for the silent-end problem (§7), and larger than anything this backend has needed so far |
| Feed tarfile archivey's own decompressor, never `r:gz` | One codec layer for every format: the same seek points, accelerators, ratio guard, diagnostics and error translation as a bare `.gz` | tarfile's built-in modes, which cover four codecs and bypass all of that |
| Classify the end by the error class of the last header parse, then by the block after the stop | tarfile does not report why it stopped, but the error passes through `_TarInfo.fromtarfile` before `next()` swallows it. That is the same answer in both access modes and needs no backward seek, which on a compressed tar would mean decoding again | Computing the next header's offset from `offset_data + size`, which is wrong for sparse members; inspecting the last block tarfile read, which streaming cannot see and which misses a PAX header longer than one block; treating every early end as a warning |
| A rejected header is `CorruptionError` whatever the policy; a missing trailer is a warning | A complete tar never stops on a rejected header, so that one is certain. A missing trailer is ambiguous by construction | One disposition for both, which is either too loud for ordinary trailer-less tars or silent about corruption |
| A zero block followed by a non-null one is a warning, not corruption (maintainer ruling, 2026-10-06) | The zero block ends the members, so the listing is whole and only the marker is damaged. GNU tar and 7-Zip list such an archive with a warning and exit 0; a damaged RAR end-of-archive block is handled the same way. `strict()` refuses it | `CorruptionError`, as before the ruling, which in random access threw away a whole listing |
| A zero-filled file is a valid empty tar | It is byte-identical to one, at every block-aligned length ([ADR 0015](../decisions/0015-zero-filled-files-are-valid-empty-tars.md)) | Refusing zero-member tars; a length rule |
| Report trailing data, do not read past it | Two archives in one file is a fact worth reporting, and listing both would present members from an archive the caller did not name | `ignore_zeros=True`, which is how `tar -i` reads concatenated archives |
| Bound the trailing-data scan at 1 MiB, as a constant | On a compressed tar the tail must be decoded to be read. A constant can become a config field later; a field cannot become a constant | Scanning to EOF; a `ListingLimits` field whose `None` would mean "unbounded", the reverse of every other field there |
| Count a sparse member's holes as output (maintainer ruling, 2026-09-25) | Extraction writes them as zeros, so they cost the disk what any decompressed byte costs, and the ratio guard is what protects the disk. Revisit if extraction ever preserves holes, as `tar -x` does, since the disk would then hold only the data | Counting only the data blocks, which would let a few hundred bytes of sparse map fill the disk |
| Refuse an out-of-order or overlapping sparse map with `UnsupportedFeatureError` (maintainer decision, 2026-10-10) | GNU tar 1.35 reads such a map, so it is valid data that archivey cannot serve, and DR-4 types that as unsupported. tarfile's `extractfile()` stitches the chunks into one run, a wrong answer (DR-1). To serve the map right, archivey would place the chunks itself, and the streaming path would buffer up to the member's logical size (DR-9). No real writer is known to produce such a map; only crafted archives hold one. Reopen if a real producer writes such maps, or if the sparse read path no longer goes through `extractfile()` | Reading the map as GNU tar does, which needs that placement and buffering. `CorruptionError`, as the first version of the check raised, dropped because DR-4 types a layout the reference tool reads as unsupported |
| Keep `extractfile()`, under one lock | It is the only sparse expansion in the tree, and it is stdlib's | Reading member bytes directly, which would need a sparse implementation |
| A backslash is part of the name | TAR is a POSIX format, and `a\b` is a legal filename there. Extraction under `STRICT` and `STANDARD` still writes it as `a/b`, the tree Windows would create, and rewrites a link target the same way so a link to that member follows it; the result is the same on every OS | Treating it as a separator in `name` the way the ZIP and 7z backends do |
| Walk headers in batches sized by what the caps have left | The cap then bounds what tarfile parses, not only what archivey keeps, at the speed of one dense pass | `getmembers()`, which parsed the whole file before the first member was counted; one header per lock hold, which alternated parsing with member construction and was slower |

## 7. Open questions

- **Whether to replace tarfile's walk with a native one.** It would validate each header
  at its offset, which lets a listing salvage past a bad header and drops tarfile's
  duplicate member list. It would not settle the missing-trailer ambiguity, which is in
  the bytes. What would answer it: whether either of those two matters to a real caller
  before 1.0. The walker is tracked internally.
- **Whether to detect v7 tars by their header checksum.** A 512-byte block whose checksum
  field matches its byte sum is strong evidence, and it is what `tarfile.is_tarfile`
  checks. It would also admit random blocks that happen to match, which the current
  magic never does. What would answer it: how often v7 tars without an extension reach
  archivey, which nothing measures.

## 8. Verify

```bash
./scripts/test.sh tests/test_tar.py tests/test_listing_limits.py \
    tests/test_review_simplicity_consistency.py tests/test_extraction.py \
    tests/test_detection.py tests/test_concurrent_multithread.py
```

| Claim | Pinned by |
| --- | --- |
| Cost matrix, plain and compressed | `tests/test_tar.py::test_plain_tar_cost`, `::test_compressed_tar_cost_and_read` |
| No member count and no report peek before a pass | `::test_member_list_not_available_without_scan`; `tests/test_review_simplicity_consistency.py::test_tar_has_no_report_peek_before_a_pass` |
| Random access needs a seekable source; streaming works on a pipe, plain and compressed | `::test_non_seekable_tar_fails_fast`, `::test_non_seekable_tar_streaming_opens_without_scanning`, `::test_non_seekable_plain_tar_stream_members`, `::test_non_seekable_tar_gz_streaming` |
| Metadata mapping, PAX `mtime`, PAX `atime` and `ctime`, libarchive birth time | `::test_member_metadata`, `::test_pax_mtime_override`, `::test_pax_atime_ctime`, `::test_pax_libarchive_creationtime_is_created` |
| `raw_name` for PAX and ustar names under a non-UTF-8 `encoding` | `::test_pax_raw_name_is_the_stored_utf8_whatever_the_encoding`, `::test_ustar_raw_name_follows_the_archive_encoding`, `::test_pax_raw_name_with_undecodable_bytes_round_trips`, `::test_gnu_long_name_under_a_global_pax_path_keeps_the_archive_codec` |
| ustar and GNU names, link targets, `uname` and `gname` are UTF-8 by default, not the locale's codec; `encoding=` decodes only bytes that are not valid UTF-8; a PAX record that is not UTF-8 falls back to that same codec | `::test_utf8_name_decodes_as_utf8_under_a_non_utf8_locale`, `::test_utf8_link_target_and_owner_decode_as_utf8_under_a_non_utf8_locale`, `::test_invalid_utf8_name_is_surrogate_escaped_under_a_non_utf8_locale`, `::test_utf8_header_name_wins_over_the_caller_encoding`, `::test_utf8_link_target_and_owner_win_over_the_caller_encoding`, `::test_caller_encoding_decodes_header_fields_that_are_not_utf8`, `::test_mixed_header_names_each_decode_by_their_own_bytes`, `::test_binary_pax_path_that_is_valid_utf8_wins_over_the_caller_encoding`, `::test_pax_raw_name_with_undecodable_bytes_round_trips`, `::test_pax_path_that_is_not_utf8_falls_back_to_the_caller_encoding` |
| Out-of-range `mtime` degrades | `::test_out_of_range_mtime_degrades_to_none` |
| A bad PAX `mtime`, `atime`, `ctime` or `LIBARCHIVE.creationtime` is `None` and reported, each once; a PAX `mtime` of `0` stays the epoch | `::test_bad_pax_time_is_reported`, `::test_several_bad_pax_times_on_one_member_are_each_reported`, `::test_pax_mtime_zero_is_the_epoch` |
| Old GNU and PAX 0.0, 0.1 and 1.0 sparse members list as sparse and read back logically | `::test_sparse_tar_eof_no_false_positive`, `::test_pax_sparse_member_is_reported_sparse` (one case per PAX encoding) |
| End classification: good, minimal and padded trailers stay silent | `::test_valid_tar_eof_silent`, `::test_minimal_eof_trailer_silent`, `::test_padded_tar_eof_no_false_positive` |
| Missing trailer warns, and raises under `RAISE` | `::test_missing_eof_blocks_warns_by_default`, `::test_missing_eof_blocks_raise_disposition_raises`, and the `_streaming_` pair |
| Rejected header, mid-archive and last block, plain, gzip and sparse | `::test_corrupt_mid_header_raises_corruption_by_default`, `::test_corrupt_final_header_raises_corruption_by_default`, `::test_corrupt_final_header_gzip_raises_corruption`, `::test_corrupt_final_header_sparse_raises_corruption` |
| A zero block then a damaged block lists and reads every member, warns, extracts everything, and raises under `strict()`, in both modes | `::test_damaged_second_eof_block_lists_every_member`, `::test_damaged_second_eof_block_gzip_lists_every_member`, `::test_damaged_second_eof_block_extracts_every_member`, `::test_damaged_second_eof_block_refused_under_strict`, `::test_zero_block_then_junk_with_no_member_stays_corruption` |
| After a damaged second block the trailing scan still runs: a bad gzip CRC raises and junk is trailing data | `::test_bad_gzip_crc_is_reported_after_a_damaged_second_eof_block`, `::test_damaged_second_eof_block_then_junk_reports_trailing_data` |
| A rejected header raises in both modes whatever follows it: members, nothing, a zero block; a negative PAX or base-256 size; a malformed PAX header as the last thing in the file | `::test_corrupt_final_header_streaming_raises_corruption`, `::test_rejected_header_raises_corruption_in_both_modes` |
| Rejected header wins over `IGNORE` and `RAISE` | `::test_corrupt_final_header_ignore_disposition_still_raises`, `::test_corrupt_mid_header_raise_disposition_still_corruption` |
| `extract_all` writes the salvageable members, then raises, in both modes | `::test_corrupt_final_header_extract_raises`, `::test_corrupt_mid_header_streaming_extract_writes_then_raises` |
| `extract_all` on `.tar.gz`/`.bz2`/`.xz` decodes once; limits still bind | `::test_extract_compressed_tar_decodes_once`, `::test_extract_enforces_listing_limits_as_members_arrive` |
| Truncation inside member data raises during iteration | `::test_truncated_tar_raises` |
| Trailing data reported, bounded, quiet on an undecodable tail; zeros pass | `tests/test_review_simplicity_consistency.py::test_trailing_data_is_reported`, `::test_trailing_data_scan_is_bounded`, `::test_compressed_tail_that_will_not_decode_ends_the_scan_quietly`, `::test_zero_padding_after_the_trailer_still_passes`, `::test_wrong_explicit_format_on_iso_reports_trailing_data` |
| Zero-filled files are empty tars; detection refuses them | `::test_legitimately_empty_tar_stays_valid`, `::test_every_block_aligned_zero_length_is_a_valid_empty_tar`, `::test_zero_filled_dot_tar_opens_empty_via_extension`, `::test_content_detection_refuses_a_zero_filled_file` |
| A PAX header's size does not drive an allocation (O15) | `tests/test_tar.py::test_extended_header_size_does_not_drive_the_allocation` |
| The listing stops reading headers at `max_members` and `max_metadata_bytes`, returns to full batches past the cap, and keeps its prefix when it fails mid-batch | `tests/test_listing_limits.py::test_tar_listing_stops_reading_headers_at_max_members`, `::test_tar_listing_stops_reading_headers_at_max_metadata_bytes`, `::test_tar_header_batch_returns_to_full_size_past_max_members`, `::test_tar_extract_all_enforces_listing_limits`; `tests/test_tar.py::test_members_report_keeps_the_prefix_when_the_walk_raises_mid_batch` |
| Links: relative, `..`, absolute, archive-relative hardlinks, duplicate names, cycles | `tests/test_tar.py::test_relative_symlink_resolves_against_link_directory` through `::test_chain_through_same_named_members_not_false_cycle` |
| Hardlink extraction: one pass, orphans, cross-device, past the link-count limit | `tests/test_extraction.py::test_tar_hardlink_shares_inode`, `::test_tar_hardlink_orphan_recovered_seekable`, `::test_tar_hardlink_orphan_forward_only_onerror`, `::test_cross_device_hardlink_reuses_sibling`; `tests/test_cross_os_extraction.py::test_hard_link_past_the_link_limit_is_copied` |
| `\` in a name or link target under `STRICT`/`STANDARD` | `tests/test_cross_os_extraction.py::test_tar_backslash_is_written_as_a_separator`, `::test_hardlink_target_backslash_becomes_a_separator`, `::test_hardlink_resolves_by_its_stored_target`, `::test_symlink_to_a_member_named_with_a_backslash_resolves`, `::test_symlink_target_backslash_cannot_climb_out` |
| A hardlink resolves backward only, in both modes | `tests/test_tar.py::test_hardlink_resolves_to_an_earlier_member_only`, `tests/test_extraction.py::test_hardlink_before_source_is_not_linked_forward` |
| `stream_members()` on a random-access compressed tar decodes once | `tests/test_audit_cross_format.py::test_compressed_tar_stream_members_decodes_once` |
| Ratio guard: static for a path, live for a piped `.tar.gz`, no live check on a plain tar | `::test_seekable_targz_uses_static_not_live`, `::test_streaming_targz_bomb_caught_by_live_ratio`, `::test_streaming_plain_tar_no_live_ratio_trip` |
| Concurrent reads through the handle lock | `tests/test_concurrent_multithread.py::test_multithread_plain_tar_open_read`, `::test_multithread_gzip_tar_open_read` |
| Inner-TAR detection over each codec, and its budget | `tests/test_detection.py::test_inner_tar_over_gzip_is_tar_gz` and its siblings, `::test_inner_tar_probe_stays_inside_the_decode_budget` |
| Passwords accepted and never consulted | `tests/test_tar.py::test_password_is_accepted_in_every_form` |
| A sparse member's extraction and its ratio (holes count, §6) | **Nothing pins it.** The page's measurement is the reproduction below |
| Pre-1970 times, on every platform | `tests/test_tar.py::test_pre_1970_mtime_lists_its_date` (PAX and GNU base-256, with a `fromtimestamp` that rejects negatives as Windows' does), `::test_pre_1970_pax_atime_lists_its_date`; `tests/test_timestamps.py` for the shared helper |

**Building fixtures.** Most TAR tests build their archives with stdlib `tarfile` in
memory, and corrupt them by hand: a header checksum byte, a truncation, a block of junk
where the trailer belongs. Stdlib cannot write sparse members, so the old GNU sparse
fixture is assembled byte by byte (`_tar_sparse_gnu`), the PAX 1.0 one is a `tarfile`
member carrying the `GNU.sparse.*` records and the map as data (`_tar_sparse_pax_1_0`),
and the PAX 0.0 and 0.1 extended headers are written by hand (`_tar_sparse_pax_0_x`),
since 0.0 repeats keys a `pax_headers` dict cannot hold.
GNU `tar` writes the real thing; BSD and Windows `tar` refuse
`--sparse`, which is why no test shells out for it. The sparse-extraction measurement in §5:

```bash
truncate -s 10M f && printf x | dd of=f bs=1 seek=5000000 conv=notrunc
tar cf sparse.tar --sparse f     # 10 240 bytes; add --format=pax for the PAX encoding
python -c "import archivey; archivey.open_archive('sparse.tar').extract_all('out')"
# _AlwaysStopResourceLimitError: Archive-wide decompression ratio 1024:1 exceeds limit max_ratio=1000:1
```

## 9. References

- POSIX.1-2017, [`pax` utility](https://pubs.opengroup.org/onlinepubs/9699919799/utilities/pax.html):
  §ustar Interchange Format (the 512-byte header, the `ustar` magic, the two zero
  blocks) and §pax Interchange Format (`x` and `g` records, `path`, `linkpath`,
  `mtime`, `atime`, `ctime`, `hdrcharset`)
- GNU tar manual, [Basic Tar Format](https://www.gnu.org/software/tar/manual/html_node/Standard.html)
  (the GNU header, `L`/`K`/`S` typeflags, base-256) and
  [Sparse Formats](https://www.gnu.org/software/tar/manual/html_node/Sparse-Formats.html)
  (old GNU, PAX 0.0, 0.1 and 1.0)
- Python [`tarfile`](https://docs.python.org/3/library/tarfile.html). The silent-end
  behaviour is `TarFile.next()`, which re-raises `InvalidHeaderError` only at offset 0
- Specs: [`format-tar`](../../openspec/specs/format-tar/spec.md) (the EOF matrix, the
  hardlink matrix and the lock boundary in full) ·
  [`safe-extraction`](../../openspec/specs/safe-extraction/spec.md) ·
  [`compressed-streams`](../../openspec/specs/compressed-streams/spec.md)
- Registers: [`known-issues.md`](../known-issues.md) §stdlib `tarfile` treats a corrupt
  non-first header as clean end-of-archive ·
  [`threat-model.md`](../threat-model.md) O1, O15 ·
  [ADR 0015](../decisions/0015-zero-filled-files-are-valid-empty-tars.md)
- Code: `internal/backends/tar_reader.py` (the whole backend) ·
  `internal/detection.py` (`_probe_inner_tar`) · `internal/streams/codecs/` (the
  decompressors tarfile reads) · `internal/extraction.py` (hardlinks, the ratio guard) ·
  `internal/naming.py`
- Handbook: [`7z.md`](7z.md) (link targets as member data, for contrast) ·
  [`zip.md`](zip.md) · [`rar.md`](rar.md) ·
  [`topics/stream-ownership.md`](../topics/stream-ownership.md)
- User-facing: [`docs/formats.md`](../../docs/formats.md#tar-and-compressed-tar) ·
  [`docs/gotchas.md`](../../docs/gotchas.md)
