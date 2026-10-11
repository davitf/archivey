# TAR

Current maintainer truth for the TAR backend, plain and compressed. A tar file is a run of
512-byte headers, each followed by that member's data, ended by zero blocks. There is no
index, no per-member compression and no checksum over data. archivey parses it with its
own header walker (`tar_parser.py`), reading either the source itself or a decompressor
archivey built. Most of what is peculiar here follows from those two facts. Registers
keep the status; this page states the behaviour and links the row.

## At a glance

| | |
| --- | --- |
| Read | Yes, with archivey's own header parser and walker (`internal/backends/tar_parser.py`), in random access and with `streaming=True`. It reads the source for a plain tar and archivey's own decompressor for a compressed one |
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

**The end is two zero blocks.** A walk stops on a zero block, on a block that is not a
header, or where the source runs out, and the walker says which (§2.2). The result has
three outcomes: a header that does not parse is corruption; a missing, short or damaged
trailer is a warning, because a complete tar written without a trailer and a tar truncated
exactly at a member boundary are the same bytes, and a zero block followed by a damaged
one still ends a whole listing; bytes after a good trailer are trailing data. An empty
tar is nothing but zeros, so a zero-filled file of any block-aligned length is a valid
empty archive ([ADR 0015](../decisions/0015-zero-filled-files-are-valid-empty-tars.md)).
Two tars joined with `cat` list as the first one plus a trailing-data diagnostic,
because the first one's trailer ends the walk.

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
any field. The walker applies all three before it hands out a member, which has three
consequences here. It keeps the name's stored bytes and where they came from, which
decides how they are decoded; `raw_name` is those bytes (§2.2). `extra["tar.pax_headers"]`
holds the merged records, global ones included, not the member's own block; it is
read-only, and members with no records of their own share one copy (§2.2). And
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

**archivey parses the headers itself.** `TarWalker` (`internal/backends/tar_parser.py`)
reads one 512-byte block at a time and returns one member per call, with the extended
headers before it (`x`, `X`, `g`, `L`, `K`) applied, or a `TarEnd` that says why the walk
stopped. It is a loop, so a chain of extended headers cannot recurse. It reads v7, ustar
and old GNU headers, base-256 numbers, PAX records and the four GNU sparse encodings, and
joins the ustar `prefix` to the name only under the ustar magic, as GNU tar does. Where
GNU tar and `tarfile` read a malformed archive differently, it follows GNU tar (DR-6)
unless a comment there says otherwise. `tar_reader.py` maps each `TarEntry` to an
`ArchiveMember` and owns the end-of-archive policy. Nothing in the walk depends on the
Python version, so the listing is the same on every supported one.

**What the walker reads.** For a plain tar, the `ArchiveSource` itself. For a compressed
tar, archivey's own codec stream; a path source goes to the codec as a path, so the codec
can use an accelerator and a static ratio. A random-access walk reads through a buffered
`SharedView` of its own, with a fixed 8 KiB read-ahead so listing cost does not change
with Python's default buffer size. The view re-seeks the shared stream before each read,
so a member read never moves the walk. A streaming walk is the only reader of the
forward stream, buffered, and member data is read through it. The reader closes every
stream it built.

**Every buffer reads the stream under it at most once per read.** It is a
`ReadAheadStream`, not `io.BufferedReader`, which asks again after a short read. A
decoder over a cut stream returns the bytes it could decode and raises
`TruncatedError` on the next read, so asking again in the same read would drop those
bytes. For the same reason the random-access codec stream does not repeat its first
error after a seek (`open_codec_stream(repeat_verdict=False)`): its views seek before
every read. Each member stream keeps its own. A cut member therefore gives its whole
readable prefix before `TruncatedError`, in either mode and in any read size. A
`read()` with no size asks for the whole member, so on a cut member it raises and
returns nothing, as the compressed-streams spec says.

**A random-access member view stops at the end of the archive** and then returns no
bytes. When a member's data runs past the end, its stream turns on the fused length
check (`expected_size`), as ISO does for a file cut by the end of the image, so a cut
plain tar member raises `TruncatedError` after its prefix. Other members keep the bare
`size`, which turns on no check.

**The first header is parsed at open**, so a file that is not a tar fails there (DR-15b):
a first block that does not parse is `CorruptionError`, and an empty file or one shorter
than a block is `TruncatedError`.

**Reads whose size the archive chooses are bounded.** An extended header is charged to the
member's `max_metadata_bytes` budget at its declared size before it is read, then read with
`read_within_reach` in 64 KiB steps, so the allocation follows the bytes that exist, not
the size field ([`threat-model.md`](../threat-model.md) O15). A member's data area is
skipped with one seek on a seekable stream. A forward-only walk reads through it in
64 KiB steps and raises `TruncatedError` at the first short read, so the skip costs the
bytes present: a 2 KiB archive declaring a 2**45-byte member fails at once instead of
looping on the declared size. On a seekable stream a seek past the end succeeds, so when
the walk finds nothing where the next header should start, it checks that the last byte
of the previous member's data area exists: a member cut short is `TruncatedError`, not a
clean end.

**How the end is classified.** The walk ends with a `TarEnd` whose `kind` says why:
`ZERO_BLOCK` (the first trailer block), `REJECTED` (a block, or a chain of headers, that
does not parse; `reason` says what), `ABSENT` (the stream ended where a header should
start) or `SHORT` (it ended inside a header block). The reason is the same in both access
modes and needs no extra read, so both modes run the same EOF check (`_verify_tar_eof`),
in this order. An `x`, `X`, `L` or `K` header right before a zero block describes no
member; the walk ends on the zero block as usual, as GNU tar 1.35 lists such an archive,
with no diagnostic. Damage inside one member's headers is not a `TarEnd` and raises
during iteration: `TruncatedError` when the stream ends inside a header, an extended
header's data or right after an extended header, and `CorruptionError` for a PAX `size`
that is not a number or a PAX sparse map that does not parse.

  1. The walk stopped on a header that does not parse (a bad checksum or number field, a
     negative size, PAX records that do not parse, a bad number in an old GNU sparse
     extension block, or an extended header followed by a block that is not a header).
     The listing was cut short, so it is `CorruptionError` whatever the diagnostic
     policy and whatever follows: more members, nothing, or a zero block (a member whose
     data starts with 512 zero bytes, which step 2 would take for the second trailer
     block).
  2. A stream that ran out where a header should start, or inside one, emits
     `ARCHIVE_EOF_MARKER_MISSING` under the ordinary policy, a warning by default. After
     a zero block the check reads the next block, the second trailer block. A null
     block is a good trailer, and a short or empty read is reported as above. A
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

**The walk stops at the listing caps.** Listing pulls one header per call and registers
the member, counting it against `ListingLimits`, before the next header is parsed, so a
header bomb stops at the member that crosses `max_members` or `max_metadata_bytes`. The
walker takes a budget for each member's headers: what is left of `max_metadata_bytes`
while the listing enforces it, and the whole cap otherwise (`stream_members()` on a
random-access reader, and every streaming walk). An extended header is charged its
declared size before it is read, and the headers of one member's chain draw from one
budget, so four 300 KB PAX headers under a 1 MiB cap raise `ResourceLimitError` before
the fourth is read. A sparse map is charged 24 bytes per entry from its entry count
before its entries are parsed: a 0.0 map by its offset records, a 0.1 map by its commas,
a 1.0 map by its count line and an old GNU map block by block. A few kilobytes of
compressed map can hold millions of entries, so the member keeps the map in two arrays
of 8-byte numbers, and registration counts it against `max_metadata_bytes` as listing
text. PAX global records are held once per global header: members with no records of
their own share one read-only snapshot, which is why `extra["tar.pax_headers"]` is
read-only, and each member is still charged for them as if copied. When the walk raises
partway, the members already registered stay, so `members_report()` keeps its salvaged
prefix. The walker keeps nothing behind the listing, so a pass holds one list of
members. On a streaming reader, `members_report()` counts members against the cap as
they arrive and raises at the one past it. `stream_members()` and
forward-only iteration are not capped, by design, and their list grows for the whole
pass.

**Member metadata** is mapped in `_to_member`:

| Field | From |
| --- | --- |
| `type` | The typeflag. `5`, and GNU's dumpdir `D` (from `tar -G`, its contents list skipped), are directories; `2` a symlink; `1` a hardlink; `0`, NUL, contiguous `7` and old GNU sparse `S` files. Everything else, including devices, FIFOs, multi-volume and volume headers and unknown types, is `OTHER`, with `extra["tar.type"]` holding the typeflag byte; a device or FIFO (`3`, `4`, `6`) also gets `extra["special_file_type"]`, the cross-format kind. TAR has no data-bearing special entry: GNU tar and libarchive ignore the size field of a device or FIFO header, so a non-zero size there is damage (`CorruptionError`), never a `FILE`. An old-style (v7) `AREGTYPE` header (typeflag NUL) whose final name ends in `/` is a directory, as in GNU tar 1.35 and 7-Zip. The final name is the one after a PAX `path` or a GNU long name. The data blocks its `size` declares are skipped, and `extra["tar.type"]` stays the stored `b"\x00"`. The skipped bytes are not reported: the member has no `size`, no `extra` key and no diagnostic for them, although DR-3 asks for one and GNU tar and 7-Zip both print the size (tracked internally) |
| `name` | The name the walker resolved: a PAX `GNU.sparse.name` or `path` record, else a GNU long name, else the header's `name` (with `prefix` joined under the ustar magic only). Decoded as below and normalized with `backslash_is_separator=False`, since a backslash is a legal POSIX filename character. `./` prefixes go and a directory gets a trailing `/`, with `MEMBER_NAME_NORMALIZED` for each change |
| `raw_name` | The stored bytes of whatever supplied the name, always. Never `None` |
| `link_target` | The link name as stored (a PAX `linkpath`, else a GNU long link name, else the header's `linkname`), decoded as below, for symlinks and hardlinks; the walker drops a link name on any other type before keeping it. A hardlink stores an archive path, so a tar made from `./d` stores `./d/b` while the member it names is listed as `d/b`. `link_target_member` is the resolved one |
| `size` | The logical size for a file, which for a sparse member is the size its sparse records declare, not the bytes stored. `None` for everything else. `compressed_size` is never set |
| `modified` | The PAX `mtime` record when there is one, parsed once with its fraction, else the header's `mtime`. A value `datetime` cannot hold is `None` plus `MEMBER_TIMESTAMP_INVALID`. So is a PAX `mtime` that is not a number: it lists as `None`, not as the Unix epoch |
| `accessed` | PAX `atime` only. A record that is not a number, or out of range, is `None` plus `MEMBER_TIMESTAMP_INVALID`, as for `modified`; the same holds for `created` and `ctime` |
| `created` | The PAX `LIBARCHIVE.creationtime` keyword, which libarchive writes when the source OS has a birth time. No other TAR writer is known to store one, so it is `None` for most archives |
| `ctime` | PAX `ctime` only. It is the inode-change time (`st_ctime`), so it never fills `created`. A libarchive tar can carry both |
| `mode`, `uid`, `gid`, `uname`, `gname` | From the header; a PAX record of the same name overrides `uid`, `gid`, `uname` or `gname`. A PAX `uid` or `gid` that is not a number is ignored and the header's value kept, as GNU tar keeps it. `mode` keeps the permission and setuid/setgid/sticky bits only, masked before `stat.S_IMODE`, so a negative or wider-than-32-bit base-256 mode cannot fail the listing |
| `is_sparse` | True for the old GNU `S` typeflag and for all three PAX sparse encodings. Sparse records on a link, device, FIFO or directory make no sparse member |
| `extra` | `tar.type` always; `special_file_type` on device and FIFO members; `tar.pax_headers` when there are any (read-only, a `dict` subclass whose changes raise `TypeError`; members with no records of their own share one per set of global records); `tar.devmajor` / `tar.devminor` for device members |

Names keep their bytes until `_to_member` decodes them, once, knowing where each came
from. A ustar or GNU field (name, link target, `uname`, `gname`) declares no encoding, so
it is read as UTF-8 when its bytes are valid UTF-8, as every format reads an undeclared
name (design rules, ruled 2026-10-07), and with the caller's `encoding=` otherwise.
Without `encoding=` the fallback is UTF-8 with `surrogateescape`, not the process
locale's codec, so a listing does not depend on the locale. A name taken as UTF-8 where
`encoding=` would have given a different one emits `MEMBER_NAME_ENCODING_INFERRED` naming
the caller's codec, as ZIP and RAR 1.5-4 do. A PAX record is decoded strictly as UTF-8
first and falls back to the archive codec (with `surrogateescape`) only when that fails,
so `encoding=` changes a PAX name only when its bytes are not UTF-8. A PAX record under
`hdrcharset=BINARY`, in its own header or in a global header before it that no later
global header reset, declares no encoding and is read as a ustar field is.

### 2.3 Member data

**A member's stream is a view of its data area.** In random access it is a `SharedView`
over the reader's byte stream (the source, or the codec stream), from the member's
`data_offset` for its `stored_size` bytes, which re-seeks the shared stream before each
read. On a compressed tar the view has an 8 KiB buffer above it, so small reads do not
each reach the decoder. A seek past the end returns the target and the next read returns `b""`, as in every
other format. In a streaming pass, and in a random-access pass whose walk is still
running, the member is read through the walk's own stream (`TarWalker.open_data`). A
sparse member is a `SparseStream` over either: it serves the logical bytes, zeros in the
holes, and reads the stored stream only inside a chunk.

**A sparse map is checked against the member's sizes first.** Every map is parsed during
the walk, a PAX 1.0 map from the first blocks of the data area, so a map that does not
parse fails the listing in every encoding. When the member is opened (streaming: on its
first read, so a consumer that skips it is unaffected), `validate_sparse_map` raises
`CorruptionError` for a negative entry, a chunk that ends past the logical size (even an
empty one), a logical size past 2**63 - 1 (no file's size), or chunks that do not add up
to exactly the bytes the member stores. The walker knows the stored size exactly, so a
map that names 1 to 511 bytes fewer than the member stores is refused too: bytes inside
a member that nothing names are damage (DR-3), and GNU tar 1.26 to 1.35 and bsdtar write
the exact sum in every encoding (maintainer decision, 2026-10-10). GNU tar 1.35 extracts
such a member without the unnamed bytes. An empty entry past the logical size loses no
bytes; it is refused because the map contradicts its own declared size (DR-1). GNU tar
1.35 refuses a chunk past the logical size in old GNU and PAX 1.0, and reads it in PAX
0.0 and 0.1.

**An out-of-order or overlapping map is `UnsupportedFeatureError`.** GNU tar 1.35 reads
both, writing each chunk at the offset the map gives. Serving the chunks in logical order
on the streaming path would mean buffering up to the member's logical size (DR-9). The
map is valid data archivey does not serve, so it is unsupported, not corrupt (DR-4); a
map that is also damaged raises `CorruptionError`. Only crafted archives are known to
hold such a map, and there is no plan to support it; revisit if a real archive appears
(§6). Empty entries are exempt from the order check, since GNU tar ends a map with
`(realsize, 0)` and the old GNU header pads its slots with `(0, 0)`. One function makes
all of these checks for the four encodings (old GNU and PAX 0.0, 0.1, 1.0), since the
walker turns each into the same `SparseMap` of offsets and lengths.

**A seek the filesystem refuses reads as the end of the data.** The walker seeks to
offsets it adds up from size fields, and a PAX or base-256 size can put one anywhere. An
offset past 2**63 - 1 is refused before the seek, with `CorruptionError`: no file has a byte
there. A smaller one can still be past the largest file the filesystem holds: ext4
(about 16 TiB) refuses the seek with `EINVAL` or `EOVERFLOW`, while APFS and a `BytesIO`
accept it and the next read finds the end. The archive is shorter than any file that
filesystem can hold, so the offset is past its end either way, and the reader raises
`TruncatedError` naming the offset. One archive then gives one error from every source
on every OS (DR-5), and it is GNU tar's answer for the same bytes (`Unexpected EOF in
archive`). The `except` holds one absolute seek to an archive-chosen offset, which is
why `EINVAL` is an archive fact here though extraction deliberately does not translate
it (`openspec/specs/safe-extraction/spec.md`).

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
good only until the pass moves on, because the walk reads through whatever the consumer
left and then reads the next header from the same stream; keeping one and reading it
later raises `ValueError` on a closed file. A
hardlink or symlink in a streaming pass resolves against members already seen.

**A hardlink resolves to an earlier member only, in both modes.** A hardlink is a
reference to a file already archived: tarfile's `_find_link_target` searches only the
members before the link and takes the last match, and `tar(1)` links to what it has
already written. The base reader never looks forward for a hardlink's target, for any
format, so a hardlink whose only same-named member comes after it has no
`link_target_member`, and opening or extracting it raises `LinkTargetNotFoundError`, in
random access as in a streaming pass. A symlink is a path, not a reference, and resolves
to the last member of that name either way.

**`MemberStreams.CONCURRENT`** puts one lock around every read of the shared byte
stream: each read the walk's buffer makes, each member read and seek, and the
end-of-archive reads. Each view re-seeks the stream and reads under the lock. The lock
makes interleaved reads correct. It does not make them parallel, and on a compressed tar
they still share one decompressor. A streaming reader takes the same lock, uncontended,
so both modes have one critical-section shape.

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

- **Header fields size reads.** An extended header's size field is charged to the
  listing budget before the read, and the read is stepped, so a 10 KiB archive cannot
  allocate 6 GiB ([`threat-model.md`](../threat-model.md) O15).
- **Every header is a member to keep.** Headers compress to a few bytes each: 300 000
  empty headers gzip to 1.8 MB. The walk stops at the member that crosses either listing
  cap (§2.2).
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
- **A corrupt header could pass for the end of the archive.** A header after the first
  that does not parse ends the walk, and archivey raises `CorruptionError` for it in
  both access modes (§2.2). What stays a warning is a missing trailer. A caller who
  needs a provably complete listing sets `ARCHIVE_EOF_MARKER_MISSING` to `RAISE`, as
  `DiagnosticPolicy.strict()` does.
- **Link targets are header text.** A symlink or hardlink target costs no decode to read,
  so resolving links at listing is free here, unlike 7z ([`7z.md`](7z.md) §2.2). It is
  still attacker text and is counted against `max_metadata_bytes`.

## 5. Sharp edges

*Where it lives*: **format**, inherent and no implementation fixes it · **archivey**,
ours.

| What you see | Where it lives | More |
| --- | --- | --- |
| `member_count` is `None`, even after listing | **format** | No index (§1). `len(reader.members())` after the walk is the count |
| Listing a `.tar.gz` takes as long as extracting it | **format** | Headers are spread through the compressed stream, so finding them decodes everything (§1) |
| Reading members of a `.tar.gz` by name is slow, and reports `STREAM_REWIND_REDECOMPRESSES` | **format** / **archivey** | Each backward seek decodes from the nearest resume point (§2.3). `stream_members()` decodes once. `[seekable]` adds resume points for gzip and bzip2 |
| A tar with no trailer warns `ARCHIVE_EOF_MARKER_MISSING` and still lists | **format** | Complete-without-trailer and truncated-at-a-boundary are the same bytes. Set the code to `RAISE` when completeness matters |
| Two tars joined with `cat` list as one archive's members plus `ARCHIVE_TRAILING_DATA` | **format** / **archivey** | The first trailer ends the walk. archivey does not read past it the way `tar -i` does (§6) |
| A byte more than 1 MiB past the trailer goes unreported | **archivey** | The trailing-data scan is an effort bound, not a guarantee (§2.2). On a compressed tar that also leaves the stream checksum unchecked, reported as `DIGEST_UNVERIFIABLE` |
| A `.tar` of nothing but zeros opens as an empty archive | **format** | That is what an empty tar is ([ADR 0015](../decisions/0015-zero-filled-files-are-valid-empty-tars.md)). `detect_format()` still refuses it |
| A v7 tar with no extension is not detected | **format** | No magic to find (§2.1). Pass `format=ArchiveFormat.TAR` |
| A hardlink's `link_target` is `./d/b` while the member it names is `d/b` | **format** / **archivey** | `link_target` is documented as stored text. Use `link_target_member` |
| A hardlink placed before the only member it names does not extract | **format** | A hardlink refers to an earlier member, as tarfile and `tar(1)` read it; `LinkTargetNotFoundError` in both modes (§2.3) |
| Extracting a sparse file refuses with a ratio error, or fills the disk with zeros | **archivey** | Holes are written as zeros and counted as output (§2.4). Measured: a 10 MiB sparse file with one byte of data is a 10 240-byte tar, and `extract_all()` refuses it at 1024:1. By design (§6); raise `max_ratio` for an archive known to hold sparse files |
| GNU tar extracts a sparse file and archivey refuses it with `UnsupportedFeatureError` | **archivey** | The sparse map is out of order or overlapping. Serving its chunks in logical order would buffer up to the member's logical size in a streaming pass, so archivey refuses the map (§2.3). Only crafted archives are known to hold one. By design (§6); there is no plan to support it, and it is revisited if a real archive appears |
| A plain member after a global PAX header carrying `GNU.sparse.realsize` or `GNU.sparse.size` lists its stored bytes; GNU tar 1.35 and `tarfile` list it at the global size | **archivey** | Only a member's own `x` records choose its sparse encoding, so a global sparse size makes no member sparse. For `[g: GNU.sparse.realsize=20][a: 3 bytes "abc"]` GNU tar and `tarfile` list `a` as 20 bytes, `abc` padded with NULs; archivey lists the 3 stored bytes. A global `GNU.sparse.major` is ignored by all three |
| A `0` (`REGTYPE`) entry named `d/` that holds data lists as the file `d`; GNU tar 1.35 and 7-Zip make it a directory | **archivey** | The `AREGTYPE` form of the same entry is a directory (§2.2), and so is a ZIP entry `d/` with data (tracked internally) |
| A member's data changed and nothing noticed | **format** | No data checksum in a plain tar (§4) |
| A streaming pass over millions of members uses memory in proportion | **archivey** | The pass keeps one list of members for `members_report()`; the walker keeps nothing behind it (§2.2) |
| `encoding=` has no effect on some names | **archivey** | PAX names are UTF-8 by definition, and a ustar or GNU name whose bytes are valid UTF-8 is read as UTF-8 too; `encoding=` decodes only bytes that are not valid UTF-8 (§2.2) |

## 6. Decisions

| Choice | Why | Rejected |
| --- | --- | --- |
| Parse headers with archivey's own walker (`tar_parser.py`) | `tarfile` does not say why a walk stopped, overwrites a sparse member's stored size with its logical size, decides on directories before it knows the final name with a check that differs between Python patch releases; about a third of the backend worked around it. The walker reports why it stopped, keeps exact sizes and name bytes, and lists the same on every Python version ([design](../../openspec/changes/archive/2026-10-10-native-tar-reader/design.md)) | Keeping `tarfile` behind overrides of its private methods and copies of its function bodies |
| Read a compressed tar through archivey's own codec stream | One codec layer for every format: the same seek points, accelerators, ratio guard, diagnostics and error translation as a bare `.gz` | A decompression path of the TAR backend's own |
| Classify the end by the walker's `TarEnd`, then by the block after the stop | The walker knows why it stopped at the block it stopped on. That is the same answer in both access modes and needs no backward seek, which on a compressed tar would mean decoding again | Computing the next header's offset from `offset_data + size`, which is wrong for sparse members; treating every early end as a warning |
| A rejected header is `CorruptionError` whatever the policy; a missing trailer is a warning | A complete tar never stops on a rejected header, so that one is certain. A missing trailer is ambiguous by construction | One disposition for both, which is either too loud for ordinary trailer-less tars or silent about corruption |
| A zero block followed by a non-null one is a warning, not corruption (maintainer ruling, 2026-10-06) | The zero block ends the members, so the listing is whole and only the marker is damaged. GNU tar and 7-Zip list such an archive with a warning and exit 0; a damaged RAR end-of-archive block is handled the same way. `strict()` refuses it | `CorruptionError`, as before the ruling, which in random access threw away a whole listing |
| A zero-filled file is a valid empty tar | It is byte-identical to one, at every block-aligned length ([ADR 0015](../decisions/0015-zero-filled-files-are-valid-empty-tars.md)) | Refusing zero-member tars; a length rule |
| Report trailing data, do not read past it | Two archives in one file is a fact worth reporting, and listing both would present members from an archive the caller did not name | `ignore_zeros=True`, which is how `tar -i` reads concatenated archives |
| Bound the trailing-data scan at 1 MiB, as a constant | On a compressed tar the tail must be decoded to be read. A constant can become a config field later; a field cannot become a constant | Scanning to EOF; a `ListingLimits` field whose `None` would mean "unbounded", the reverse of every other field there |
| Count a sparse member's holes as output (maintainer ruling, 2026-09-25) | Extraction writes them as zeros, so they cost the disk what any decompressed byte costs, and the ratio guard is what protects the disk. Revisit if extraction ever preserves holes, as `tar -x` does, since the disk would then hold only the data | Counting only the data blocks, which would let a few hundred bytes of sparse map fill the disk |
| Refuse an out-of-order or overlapping sparse map with `UnsupportedFeatureError` (maintainer decision, 2026-10-10) | GNU tar 1.35 reads such a map, so it is valid data that archivey does not serve, and DR-4 types that as unsupported. To serve it, the sparse stream would place chunks out of order, and the streaming path would buffer up to the member's logical size (DR-9). No real writer is known to produce such a map; only crafted archives hold one. Reopen if a real producer writes such maps | Reading the map as GNU tar does, which needs that placement and buffering. `CorruptionError`, as the first version of the check raised, dropped because DR-4 types a layout the reference tool reads as unsupported |
| Refuse a sparse map whose chunks name 1 to 511 bytes fewer than the member stores (maintainer decision, 2026-10-10) | Bytes inside a member that nothing names are damage (DR-3), and every writer checked (GNU tar 1.26 to 1.35 in all five forms, bsdtar 3.7.2) writes the exact sum. Revisit if a writer is found that leaves such slack | Reading the slack silently, as GNU tar does (DR-6) |
| A backslash is part of the name | TAR is a POSIX format, and `a\b` is a legal filename there. Extraction under `STRICT` and `STANDARD` still writes it as `a/b`, the tree Windows would create, and rewrites a link target the same way so a link to that member follows it; the result is the same on every OS | Treating it as a separator in `name` the way the ZIP and 7z backends do |
| A device, FIFO or socket header with a non-zero size is `CorruptionError` at the header | The typeflag is the structure, so the entry has no data. The walker follows GNU tar and libarchive: it ignores the size field of typeflags `3`, `4` and `6` and reads the next header right after this one. Non-zero declared bytes then fail as a header, and an all-zero payload reads as the end-of-archive marker, which would drop every later member without a word. Refusing at the header makes both shapes the same damage | Reporting the size and resyncing at the next header, as GNU tar does ("Skipping to next header", exit 2) and as DR-1 would prefer. Not done for a crafted-only shape |
| Walk one header per pull | Each member is registered, and counted against the caps, before the next header is parsed, so the caps bound what the walker parses, not only what archivey keeps | Batches of headers per lock hold, which come back only if the listing benchmark needs them |

## 7. Open questions

- **Whether to salvage past a rejected header.** The walker validates each header at its
  offset, so a listing could resync past a bad one, as GNU tar does ("Skipping to next
  header"), instead of raising `CorruptionError` after the members before it. It would
  not settle the missing-trailer ambiguity, which is in the bytes. What would answer it:
  whether a real caller needs it; `IDEAS.md` lists salvage mode as *Needs design*.
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
| A member seek past its end returns the target, as in every other format | `tests/test_tar.py::test_a_member_stream_seeks_past_its_end_like_a_file` |
| A cut compressed member gives its whole readable prefix before `TruncatedError`, in any read size, streaming or random access | `tests/test_tar.py::test_a_cut_compressed_member_delivers_its_prefix_in_both_modes`; `tests/test_readahead_stream.py` |
| A `read()` with no size on a cut member raises and returns nothing, plain or compressed, in every mode | `tests/test_tar.py::test_a_whole_read_of_a_cut_member_raises_with_nothing_in_every_mode` |
| A plain tar member cut by the end of the archive raises `TruncatedError` through `read` and `open`, with no listing first | `tests/test_tar.py::test_a_cut_plain_tar_member_raises_on_read_without_a_listing`, `::test_a_cut_plain_tar_member_delivers_its_prefix_then_raises` |
| Cost matrix, plain and compressed | `tests/test_tar.py::test_plain_tar_cost`, `::test_compressed_tar_cost_and_read` |
| No member count and no report peek before a pass | `::test_member_list_not_available_without_scan`; `tests/test_review_simplicity_consistency.py::test_tar_has_no_report_peek_before_a_pass` |
| Random access needs a seekable source; streaming works on a pipe, plain and compressed | `::test_non_seekable_tar_fails_fast`, `::test_non_seekable_tar_streaming_opens_without_scanning`, `::test_non_seekable_plain_tar_stream_members`, `::test_non_seekable_tar_gz_streaming` |
| Metadata mapping, PAX `mtime`, PAX `atime` and `ctime`, libarchive birth time | `::test_member_metadata`, `::test_pax_mtime_override`, `::test_pax_atime_ctime`, `::test_pax_libarchive_creationtime_is_created` |
| `raw_name` for PAX and ustar names under a non-UTF-8 `encoding` | `::test_pax_raw_name_is_the_stored_utf8_whatever_the_encoding`, `::test_ustar_raw_name_follows_the_archive_encoding`, `::test_pax_raw_name_with_undecodable_bytes_round_trips`, `::test_pax_member_repeating_a_global_binary_charset_reads_as_binary`, `::test_global_pax_path_wins_over_a_gnu_long_name` |
| ustar and GNU names, link targets, `uname` and `gname` are UTF-8 by default, not the locale's codec; `encoding=` decodes only bytes that are not valid UTF-8; a PAX record that is not UTF-8 falls back to that same codec | `::test_utf8_name_decodes_as_utf8_under_a_non_utf8_locale`, `::test_utf8_link_target_and_owner_decode_as_utf8_under_a_non_utf8_locale`, `::test_invalid_utf8_name_is_surrogate_escaped_under_a_non_utf8_locale`, `::test_utf8_header_name_wins_over_the_caller_encoding`, `::test_utf8_link_target_and_owner_win_over_the_caller_encoding`, `::test_caller_encoding_decodes_header_fields_that_are_not_utf8`, `::test_mixed_header_names_each_decode_by_their_own_bytes`, `::test_binary_pax_path_that_is_valid_utf8_wins_over_the_caller_encoding`, `::test_pax_raw_name_with_undecodable_bytes_round_trips`, `::test_pax_path_that_is_not_utf8_falls_back_to_the_caller_encoding` |
| Out-of-range `mtime` degrades | `::test_out_of_range_mtime_degrades_to_none` |
| A bad PAX `mtime`, `atime`, `ctime` or `LIBARCHIVE.creationtime` is `None` and reported, each once; a PAX `mtime` of `0` stays the epoch | `::test_bad_pax_time_is_reported`, `::test_several_bad_pax_times_on_one_member_are_each_reported`, `::test_pax_mtime_zero_is_the_epoch` |
| Old GNU and PAX 0.0, 0.1 and 1.0 sparse members list as sparse and read back logically | `::test_sparse_tar_eof_no_false_positive`, `::test_pax_sparse_member_is_reported_sparse` (one case per PAX encoding) |
| A size field past what the filesystem can seek to is `TruncatedError` naming the offset, from a path, a `BytesIO` and a stream that refuses the seek as ext4 does; another errno propagates | `::test_size_past_filesystem_limit_is_truncation`, `::test_refused_seek_through_open_archive_is_truncation`, `::test_refused_seek_is_truncation_naming_the_offset`, `::test_refused_seek_with_other_errno_propagates` |
| End classification: good, minimal and padded trailers stay silent | `::test_valid_tar_eof_silent`, `::test_minimal_eof_trailer_silent`, `::test_padded_tar_eof_no_false_positive` |
| Missing trailer warns, and raises under `RAISE` | `::test_missing_eof_blocks_warns_by_default`, `::test_missing_eof_blocks_raise_disposition_raises`, and the `_streaming_` pair |
| Rejected header, mid-archive and last block, plain, gzip and sparse | `::test_corrupt_mid_header_raises_corruption_by_default`, `::test_corrupt_final_header_raises_corruption_by_default`, `::test_corrupt_final_header_gzip_raises_corruption`, `::test_corrupt_final_header_sparse_raises_corruption` |
| A zero block then a damaged block lists and reads every member, warns, extracts everything, and raises under `strict()`, in both modes | `::test_damaged_second_eof_block_lists_every_member`, `::test_damaged_second_eof_block_gzip_lists_every_member`, `::test_damaged_second_eof_block_extracts_every_member`, `::test_damaged_second_eof_block_refused_under_strict`, `::test_zero_block_then_junk_with_no_member_stays_corruption` |
| After a damaged second block the trailing scan still runs: a bad gzip CRC raises and junk is trailing data | `::test_bad_gzip_crc_is_reported_after_a_damaged_second_eof_block`, `::test_damaged_second_eof_block_then_junk_reports_trailing_data` |
| A rejected header raises in both modes whatever follows it: members, nothing, a zero block; a negative PAX or base-256 size; a malformed PAX header as the last thing in the file | `::test_corrupt_final_header_streaming_raises_corruption`, `::test_rejected_header_raises_corruption_in_both_modes` |
| A device or FIFO header declaring data is `CorruptionError` at the header, in both modes, and a sized header over a null payload no longer ends the listing early | `::test_a_sized_fifo_header_is_corruption` |
| Rejected header wins over `IGNORE` and `RAISE` | `::test_corrupt_final_header_ignore_disposition_still_raises`, `::test_corrupt_mid_header_raise_disposition_still_corruption` |
| `extract_all` writes the salvageable members, then raises, in both modes | `::test_corrupt_final_header_extract_raises`, `::test_corrupt_mid_header_streaming_extract_writes_then_raises` |
| `extract_all` on `.tar.gz`/`.bz2`/`.xz` decodes once; limits still bind | `::test_extract_compressed_tar_decodes_once`, `::test_extract_enforces_listing_limits_as_members_arrive` |
| Truncation inside member data raises during iteration | `::test_truncated_tar_raises` |
| Trailing data reported, bounded, quiet on an undecodable tail; zeros pass | `tests/test_review_simplicity_consistency.py::test_trailing_data_is_reported`, `::test_trailing_data_scan_is_bounded`, `::test_compressed_tail_that_will_not_decode_ends_the_scan_quietly`, `::test_zero_padding_after_the_trailer_still_passes`, `::test_wrong_explicit_format_on_iso_reports_trailing_data` |
| Zero-filled files are empty tars; detection refuses them | `::test_legitimately_empty_tar_stays_valid`, `::test_every_block_aligned_zero_length_is_a_valid_empty_tar`, `::test_zero_filled_dot_tar_opens_empty_via_extension`, `::test_content_detection_refuses_a_zero_filled_file` |
| A PAX header's size does not drive an allocation (O15); a chain of extended headers draws on one budget, charged before each read | `tests/test_tar.py::test_extended_header_size_does_not_drive_the_allocation`, `::test_extended_header_over_the_metadata_cap_is_refused_unread`; `tests/test_tar_parser.py::test_extended_header_is_charged_before_it_is_read`, `::test_extended_header_chain_shares_one_budget` |
| The parser reads each header encoding and refuses each damaged one; the walk ends cleanly on an extended header before the end marker and with `TruncatedError` on one at the end of the stream | `tests/test_tar_parser.py` (a table per encoding and per rejection reason), `::test_extended_header_before_the_end_marker_ends_the_walk`, `::test_extended_header_at_the_end_of_the_stream_is_truncation`; fuzzed by `tests/fuzz_tar_parser.py` |
| On well-formed archives written by `tarfile` and GNU tar, the walker lists the same members and bytes as `tarfile`. Where it follows GNU tar on purpose (no `prefix` outside the ustar magic, a GNU dumpdir as a directory, a PAX `uid` that is not a number ignored), a test pins the difference | `tests/test_tar_parser_differential.py`, `::test_gnu_incremental_names_have_no_prefix`; `tests/test_tar.py::test_gnu_dumpdir_entry_is_a_directory`, `::test_pax_id_that_is_not_a_number_keeps_the_header_value` |
| A sparse member serves its logical bytes, holes as zeros, over seekable and forward-only stored streams; each map is checked against exact sizes | `tests/test_sparse_stream.py`; `tests/test_tar_parser.py::test_validate_sparse_map` |
| The listing stops reading headers at `max_members` and `max_metadata_bytes`, and keeps its prefix when the walk fails; a pass holds one entry per member | `tests/test_listing_limits.py::test_tar_listing_stops_reading_headers_at_max_members`, `::test_tar_listing_stops_reading_headers_at_max_metadata_bytes`, `::test_tar_extract_all_enforces_listing_limits`; `tests/test_tar.py::test_a_pass_holds_one_entry_per_member`; `tests/test_tar.py::test_members_report_keeps_the_prefix_when_the_walk_raises`; extended-header chains, sparse maps, shared and read-only global records in both modes: `tests/test_tar_header_memory.py` |
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
- Python [`tarfile`](https://docs.python.org/3/library/tarfile.html), the fixture writer
  and the differential test's second reader
- Specs: [`format-tar`](../../openspec/specs/format-tar/spec.md) (the EOF matrix, the
  hardlink matrix and the lock boundary in full) ·
  [`safe-extraction`](../../openspec/specs/safe-extraction/spec.md) ·
  [`compressed-streams`](../../openspec/specs/compressed-streams/spec.md)
- Registers: [`threat-model.md`](../threat-model.md) O1, O15 ·
  [ADR 0015](../decisions/0015-zero-filled-files-are-valid-empty-tars.md)
- Code: `internal/backends/tar_parser.py` (header parser and walker) ·
  `internal/backends/tar_reader.py` (members, member streams, the end-of-archive checks)
  · `internal/streams/streamtools/sparse.py` (`SparseStream`) · `internal/detection.py`
  (`_probe_inner_tar`) · `internal/streams/codecs/` (the decompressors the walker
  reads) · `internal/extraction.py` (hardlinks, the ratio guard) · `internal/naming.py`
- Handbook: [`7z.md`](7z.md) (link targets as member data, for contrast) ·
  [`zip.md`](zip.md) · [`rar.md`](rar.md) ·
  [`topics/stream-ownership.md`](../topics/stream-ownership.md)
- User-facing: [`docs/formats.md`](../../docs/formats.md#tar-and-compressed-tar) ·
  [`docs/gotchas.md`](../../docs/gotchas.md)
