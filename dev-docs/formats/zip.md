# ZIP

Current maintainer truth for the ZIP backend: what the format forces, who does which
part of the work, and where the sharp edges are. Registers keep the status — this page
states the behaviour and links the row.

## At a glance

| | |
| --- | --- |
| Read | Yes |
| Write | **Not shipped**, for any format — no `archivey.create`, no writer module ([writing design](../investigations/archive-writing-design.md)) |
| Source | Seekable, in both access modes; a pipe under `streaming=True` only, read forward (§2.2.2) |
| Listing cost | `INDEXED`; `REQUIRES_SCANNING` from a pipe |
| Access cost | `DIRECT` |
| Stream capability | `SEEKABLE`; `FORWARD_ONLY` from a pipe |
| Core dependencies | None — ZIP reads on a zero-dependency install |
| Optional | `[recommended]`: Deflate64 (`inflate64`), PPMd (`pyppmd`), Zstd (`backports.zstd`, stdlib on 3.14+), WinZip AES (`cryptography`) |
| Refuses | A pipe under `streaming=False` · from a pipe, a bit-3 LZMA member without the end marker (§2.2.2) · Info-ZIP spanned sets (7-Zip `.zip.NNN` byte splits are joined, §2.2) · unknown compression methods, at read · LZMA with `lc + lp` over 4 and PPMd restore method 2, at read (§2.3) · AES without `cryptography` · PKWARE Strong Encryption, at read (§2.3) |

The extras are named for what they provide, not for ZIP, because every one of those
codecs is shared with 7z or TAR. See [`packaging-and-extras`](../../openspec/specs/packaging-and-extras/spec.md).

## 1. Shape

Four properties generate most of this page. Everything below is a consequence of one of
them.

```
[ prefix? ]  [ LFH · data · DD? ] × n   [ CDH ] × n   [ ZIP64 EOCD + locator ]?  [ EOCD ]
                  ▲                       │  ▲                                      │
                  └── header_offset ──────┘  └────── offset_cd ─────────────────────┤
                                                     search backwards ≤ 65 558 B ◄──┘
```

| Part | Signature | What it is |
| --- | --- | --- |
| **prefix** | — | Arbitrary leading bytes. **Not part of the ZIP structure**: no field records it or its length, and nothing inside the archive has to change for it to be there. See §3 |
| **LFH** — local file header | `PK\x03\x04` | One per member, immediately before that member's compressed bytes. Carries the name, extra fields, method and flags, and — unless bit 3 is set — the sizes and CRC. Its metadata is a *copy*; the CDH is authoritative |
| **DD** — data descriptor | `PK\x07\x08`, optional | Present only when general-purpose bit 3 is set: the CRC and both sizes, written *after* the data because a streaming writer did not know them in advance. The same signature at offset 0 means something else — the split-archive spanning marker |
| **CDH** — central directory header | `PK\x01\x02` | One per member, all of them together near the end; the run of CDHs is *the central directory*. Each points back at its LFH through `header_offset` and adds what the LFH has no room for: external attributes (mode), version made by (create system), the member comment, the starting disk |
| **ZIP64 EOCD** + locator | `PK\x06\x06`, `PK\x06\x07` | Wider versions of the EOCD fields, present when a count or an offset does not fit in 32 bits |
| **EOCD** — end of central directory | `PK\x05\x06` | 22 fixed bytes plus an optional comment: total entries, the size of the CDH run, `offset_cd` (where that run starts), the disk numbers, the comment length. The only record found by *position* rather than by a pointer |

archivey takes every member field from the CDH and reads the LFH only to locate the data
(§2.3). Two exceptions read the LFH for more. When the two copies of the name differ, as
bytes, the member keeps the CDH's name and reports `MEMBER_LOCAL_NAME_DIFFERS`; stdlib
refused it. When the CDH stores CRC-32 0 for a file or link that is not WinZip AE-2 and
the LFH a real value, the LFH's is the member's CRC-32, reported as
`MEMBER_LOCAL_CRC_USED` (§5).

**The index is at the end.** The central directory is authoritative and is found by
scanning backwards from the last bytes for the EOCD. So:
reading the structure requires positioning at the end, which is why a non-seekable source
is read another way, forward over the local headers with the directory last, and only
under `streaming=True` (§2.2.2); a ZIP can be preceded by arbitrary bytes without any of
its own numbers changing meaning, which is why prefixed ZIPs work (§3); appending is
expressed by writing a new directory at the end, which is why in-place update is possible
in the format and refused here (§6); and a scan that lands on the wrong `PK\x03\x04` is
usually self-correcting, because the reader still finds the real EOCD from the tail
(§2.1).

The backwards search bound is not configurable. A valid record with no trailing bytes
begins at most 65535 + 22 bytes before the end, since `comment_length` is a `uint16`.
Archivey searches one byte further, 65 558 bytes, because that is stdlib's older window
(3.11 and early 3.12 releases; 3.13 and later 3.12 releases dropped the byte). Trailing
bytes are not comment bytes, so the extra byte does find a record: a maximal comment
followed by one junk byte. When the reader used stdlib, that archive opened or not
depending on the Python version; it now lists on every version, and the junk byte is
reported as trailing data.

**Bytes after the end are reported.** The archive ends where the end record's declared
comment ends. A non-zero byte after that is `ARCHIVE_TRAILING_DATA`
(`expected_marker="zeros_to_eof"`, `format="zip"`, `observed_bytes` counted from the
comment's end), found at open by the scan TAR, 7z, RAR and ISO share
(`internal/trailing_scan.py`), or from a pipe at the end of the pass; zero padding is
silent and `STRICT` refuses (DR-3). Bytes inside the declared comment are the comment,
whatever they look like. A tail longer than the search window hides the end record from
the search; the reader then walks the local headers from the start (the pipe's walk,
seeking over sized members and scanning bit-3 ones for their descriptor, decoding
nothing) and steps over the directory and the records after it to the end record
(`step_over_directory`). That open costs up to one read of the file, and each
local header counts toward `max_members`, as on a pipe. A pipe steps over the records
after its pass, from the bytes it keeps right after the members.

**Members are independent.** Each member has its own LFH and its own compressed byte range,
with no cross-member state. So reaching any member is a seek rather than a walk (`DIRECT`),
two members can be open at once because neither decoder depends on the other's state, and
there is no solid-block cost model. A solid 7z or RAR block gives you none of that.

Seeking *within* a member is a different question and the answer is no: a compressed member
decodes sequentially, so a backward seek re-decodes from the start of the member, or from the
nearest seek point when the deflate accelerator is in play. Only STORED members are random
access in the real sense. Past a megabyte of re-decoding the stream says so, with
`STREAM_REWIND_REDECOMPRESSES`. **Seekable member streams** and **concurrent member streams**
are both off by default even here — see §6.

**Sizes and the CRC may arrive after the data.** With general-purpose bit 3 set, the LFH
carries zeros and a DD follows the compressed bytes. The CDH carries the real values, so
a random-access read is unaffected; a forward-only reader sees them late, and has to find
where the data ends without them (§2.2.2). This is also why the ZipCrypto verification
byte is the high byte of the DOS time rather than of the CRC when bit 3 is set.

**Names are bytes plus one unreliable flag.** General-purpose bit 11 declares UTF-8;
unflagged names are nominally CP437. Producers set the flag on names that are not UTF-8
and omit it on names that are, and the flag alone does not recover the intent. The one
in-band oracle is optional: Info-ZIP's Unicode Path extra field (`0x7075`) stores the name
again as UTF-8 beside a CRC-32 of the header's bytes, so a renamed entry with a stale field
is detectable. Without it, decoding is a judgement (§2.2), `raw_name` keeps the stored
bytes so a wrong decode can be undone, and a flag the bytes contradict is overruled (§2.2).

## 2. The pipeline here

Each stage: who does the work, what is ZIP-specific rather than general, what is refused.

### 2.1 Identify

ZIP declares three magics at offset 0 — `PK\x03\x04` (local header), `PK\x05\x06` (empty
archive) and `PK\x07\x08` (spanned marker) — and six extensions: `.zip`, `.jar`, `.pyz`,
`.whl`, `.apk`, `.cbz`. Comic-book aliases: `.cbr` → RAR, `.cbz` → ZIP, `.cbt` → TAR,
`.cb7` → 7z. Magic wins; a ZIP named `.cbr` emits `FORMAT_EXTENSION_CONFLICT` (see
[`rar.md`](rar.md) §2.1).

When searching a prefixed archive for a ZIP, archivey looks only for the local header
(`PK\x03\x04`). The other two magics are not useful search targets: `PK\x05\x06` would
appear before a local header only for an empty archive, and `PK\x07\x08` is the spanning
marker — 7-Zip's own `-sfx -v` writes the stub as a standalone executable and the volumes
as separate `.zip.NNN` files, so a concatenated stub-plus-spanned-ZIP is not the shape that
tool produces. Measured: `7z a -tzip -sfx -v40k` left a 416 KB stub containing **no ZIP
signature at all** beside parts that join into an ordinary byte-split ZIP starting with
`PK\x03\x04` and carrying no spanning marker anywhere. Neither magic carries fields a cheap
check can validate the way a local header can, and the survey in
[`topics/prefixed-archives.md`](../topics/prefixed-archives.md) §4 found every `PK\x05\x06`
match in a binary to be a string constant parsing to nonsense.

A local-header hit is confirmed by `validate_zip_local_header` (`internal/backends/zip_detect.py`)
before it is reported: version-needed in range, no reserved general-purpose bits, a known
method id, a non-empty name, and name+extra within the source.

**The first of those does nearly all the work.** `PK\x03\x04` occurs 148 times across
3 332 ELF and PE files under `/usr/bin`, `/usr/lib`, `/usr/local` and `/opt`, and the
validator rejects every one on version-needed alone, which must fall in 10–99 and instead
reads as whatever the surrounding bytes happen to be. Twenty-three of the 148 are the magic
followed by a run of zeros — so that shape is real rather than a story about padding, and
it fails the same check, since 0 is below the floor. The name and name+extra checks never
fired on this corpus at all; they are there for shapes it does not contain, such as a
header truncated by the end of the file.

The validator's method table is deliberately **wider** than the set archivey can decode: a
member using a method we refuse to read is still a ZIP, and identity is not a support
claim.

Two things are ZIP-specific rather than general in the shared detector:

- Prefixed ZIPs are found only when the leading bytes look like an executable or script
  (`MZ`, ELF, or `#!` at offset 0). That gate is what keeps the forward search from reading
  up to 2 MiB of every file a caller opens. A hit is reported as `ZIP` with a
  `payload_offset`, ahead of the content probes (the tier that reports headerless stream
  codecs), so a stub with a ZIP behind it comes back as a ZIP rather than as a guess at the
  stub's own bytes. `THOROUGH` does not lift the gate today — `THOROUGH_BUDGET` raises probe
  links, index bytes and the completion window but keeps the same 2 MiB window, and the gate
  itself is unconditional (`detection.py:609`); a gate-free exhaustive scan is designed and
  not shipped. The cue set, the scan window, the budget tiers and why a hit is
  validated are shared with 7z and RAR —
  [`topics/prefixed-archives.md`](../topics/prefixed-archives.md). What is ZIP's alone: the
  local-header search and its validator above, the offset conventions in §3, and the fact
  that ZIP is the only format that can locate itself from the end.
- A **tail probe** — locating the EOCD directly instead of scanning forward — is designed and
  not shipped, and ZIP is the only format it can serve. Until it lands, a prefixed ZIP behind
  bytes that look like neither an executable nor a script (a JPEG polyglot, a plain
  concatenation) is not detected, though `open_archive(..., format=ZIP)` reads it. The probe
  cannot be gated the same way: a ZIP an executable cue would reach is already found. So it
  means one tail read on every source — free on a hit, since the backend reads the EOCD
  anyway, and pure waste on a miss, which is most files. Calling `open_archive` rather than
  `detect_format()` does not change that arithmetic; it runs the same detection and wastes
  the same read. What gates it is that nobody has measured what that wasted seek costs on a
  cold cache or a remote source — not false positives, which validation handles.

### 2.2 Open and list

archivey's own parser (`internal/backends/zip_parser.py`, ADR
[0020](../decisions/0020-native-zip-reader.md)) finds the end record at open and walks
the central directory (the CDH run) as members are listed. Open reads nothing else but
the trailing-data scan, unless the tail search finds no end record: then open walks the
local headers from the start (§1, and the "no end record" row below). Each listed member
keeps a small locator (`_ZipLocator`: offsets, sizes, CRC, flags, method, and what its
open needs), not the directory entry, so an entry's extra field is not kept alive.
`reader.get()` and name lookup are satisfied from the listing with no further archive
I/O.

The walk records each entry's local-header offset for the overlap guard (§4) and counts
the entries for `member_count`. Every member open comes after that walk has ended: a
streaming pass over a seekable source walks the whole directory before its first
member's data, so it reads the directory once and then moves forward only. Only
`info.member_count` read before the listing runs one index pass over the directory
instead. Both walks charge `ListingLimits.max_members` in every mode, because each keeps
an offset per entry; an end record that declares more entries than the limit, in a
directory large enough to hold them (46 bytes each), is refused at open. A count the
directory cannot hold is a wrong field: the walk charges the entries really there, and
the mismatch is reported below. Both walks stop at the same entry, at the limit as at
damage, and the members before the stop read with the overlap bounds of the entries
before it.

**A damaged directory lists up to the damage, then the local headers past it.** An entry
with a wrong signature, or whose 46-byte fixed header the directory cuts short, raises
`CorruptionError` after the members before it, as a cut TAR or RAR does (DR-2), and
after the members the local headers hold past it: the headers are walked once, as for a
ZIP with no end record, and a member the directory already listed (by offset) is not
listed again. Those keep what their local header holds and nothing the directory alone
has (mode, host, comment), as a read from a pipe lists them, so the two reads write the
same files (davi, 2026-10-14). `members_report()` carries both, iteration and
`extract_all()` deliver those members and then raise, and `member_count` is `None`. The
overlap guard keeps one bound per listed member: a directory entry before the damage
that points at no local header bounds nothing, and one over a real header the walk
stepped over (inside another member's stated data) still bounds its neighbours, so two
entries over it stay an overlap. A walk over the local headers that meets a listing
limit raises the limit instead. One that cannot get past a member's data lists that
member, whose read fails, never as `TruncatedError` (the end record follows it), and the
listing still raises the damage; the pass meets that error first and raises it. A name,
extra field or comment that runs past the directory's end can only be the last entry's,
so it lists and is reported with the end-record checks below. A local header that the
file cuts short means the directory pointed at the wrong place (the directory follows
every local header), so it is `CorruptionError` too, never `TruncatedError`.

**"Version needed to extract" decides nothing.** Stdlib refused an archive whose entry
said it needed more than 6.3; 7-Zip 23.01 reads such a stored member and Info-ZIP
`unzip` 6.0 skips it. archivey lists and reads it: what a member needs is named by its
method and flags, checked when it opens (DR-4).

**Split sets are settled before anything else, and the two conventions get opposite
answers.** 7-Zip's `.zip.NNN` parts (and SFX `.exe.NNN` slices of the same `-v` split)
are byte slices of one finished archive, so a
complete set is concatenated by `internal/volumes.py` and read as the ordinary
single-disk ZIP it is — the same code path and the same regex that already joined
`.7z.NNN`, because it is the same `-v` flag doing the same slicing. Path parts
are sized with `stat()` and the joiner keeps a small cache of Path handles
(size and miss cost: [stream-ownership](../topics/stream-ownership.md)). The stub
`name.exe` beside those parts is not a sibling; if it has no archive magic,
`open_archive` follows it to `name.exe.001` or `name.zip.001` (including under
`format=ZIP`). Info-ZIP's
`.z01 … .zip` is a genuinely spanned set whose entries are addressed by
`(disk, offset-within-disk)`, so it keeps refusing. §3 has the producer detail.

Two files with the same part number in different padding (`x.zip.002` and a stray
`x.zip.0002`), or in different case on a case-sensitive filesystem (`X.ZIP.002`), are one
part, not a repeat: discovery takes the name opened, then the one padded and spelled like
the name opened, as 7-Zip does when it predicts the next part's name, and falls back to
the lowest name, never listing order (measured, 7-Zip 23.01 opens `x.7z.001` beside a stray
`x.7z.0002` and reads `.002`; it refuses a set renamed to mixed widths, which archivey
still joins). The same rule picks RAR `.partN` volumes ([`rar.md`](rar.md) §2.2). An
explicit sequence naming both is still refused as a repeated part.

A lone numbered part (`.zip.NNN` / `.exe.NNN` / `.7z.NNN` with no siblings) is
`TruncatedError` naming the missing parts — the same incomplete-set error as a
gap, not the ZIP "not supported" message. A part that is not on disk itself is
`FileNotFoundError`, like any other missing path. Info-ZIP `.zNN` stays
`UnsupportedFeatureError` with the rejoin-first text. Both run in `open_archive`
before detection, because middle parts have no magic at offset 0 and detection
alone would raise `FormatDetectionError`. When the parser reads the end record:
non-zero EOCD disk fields (the ZIP64 end record's when there is one, else the
classic record's, with `0xFFFF` treated as the ZIP64 sentinel),
which is what catches Info-ZIP's final `.zip` part — it would list cleanly, because it
holds the central directory — and a ZIP64 locator claiming more than one disk. Both
raise `UnsupportedFeatureError` from the parser itself.

Two things narrow the filename refusals. A joined set keeps part one's name, so they key
off *whether the set was joined*, not off the name alone; otherwise they would fire on
the very set `open_archive` had just rejoined. And they defer to an explicit non-ZIP /
non-7z `format=`, which must be honoured or refused as a *format conflict* rather than as
a volume error. Nameless streams are out of scope for them entirely.

`ArchiveInfo.is_multivolume` is `True` for a joined set and
`ArchiveInfo.extra["zip.volume_count"]` carries the part count, matching what 7z reports
for `.7z.NNN`. Both live on `ArchiveInfo`, not on `ArchiveMember` — `member.extra` is the
more reachable of the two and stays empty here. They describe how the archive arrived,
not its ZIP structure, which is single-disk.

**Name decoding.** A set bit 11 is honoured as UTF-8 when the bytes are valid UTF-8. An
unflagged name is decoded as UTF-8 when the bytes are valid UTF-8, and otherwise with
the caller's `encoding=` when one was passed, else with
`ArchiveyConfig.zip_unflagged_fallback_encoding` (default `cp437`, which decodes every
byte, so no `UnicodeDecodeError` escapes). Both decode with `surrogateescape`. The
parser hands the reader the stored bytes, so nothing is decoded twice. A flagged name
that is not valid UTF-8 is decoded as an unflagged name that is not UTF-8 would be
(`encoding=`, else the fallback), with `MEMBER_NAME_ENCODING_INFERRED` naming that codec
and `declared_encoding="utf-8"`: the flag is the one claim the bytes can check, and they
refute it. The Unicode Path field is not consulted for it, as for any flagged name. This
departs from stdlib's `metadata_encoding` and from `unzip -O`, which apply the codec to
every unflagged name: a valid UTF-8 name wins over `encoding=` in every format (design
rules, ruled 2026-10-07), so a legacy `encoding=` fixes the legacy names in a mixed
archive without garbling its UTF-8 ones.

Choosing UTF-8 for an unflagged name emits `MEMBER_NAME_ENCODING_INFERRED`; a pure-ASCII
name does not, because ASCII decodes identically under both and nothing was overridden.
The sniff is validation rather than guessing: UTF-8 is self-checking, so a clean decode is
near-conclusive evidence. No equivalent is possible for the legacy tail — see §5.

Before any of that, an unflagged name with a Unicode Path extra field (`0x7075`) in its
central-directory entry is named by the field when the field is version 1, its CRC-32
matches the stored name bytes, and its name is non-empty valid UTF-8. It outranks the sniff
and an explicit `encoding=` alike, as in 7-Zip, which also consults it only for unflagged
names and before its code page switch. Info-ZIP `unzip` and stdlib `zipfile` 3.12+
(`ZipInfo.filename`) use it too. `raw_name` is then the field's UTF-8 bytes, so `name` stays
`raw_name` decoded, and the header's bytes go to `extra["alternate_raw_name"]`; no
diagnostic, since the field declares its encoding. A stale field (CRC mismatch) or an
unknown version is not about this name and is ignored like any unknown record. A
**malformed** field is dropped with `MEMBER_HEADER_RECORD_SKIPPED` (`record="unicode_path"`,
`record_id=0x7075`) and the name decoded as if it were absent: one shorter than its
version byte and CRC, or one whose CRC matches but whose name is empty, not UTF-8, or
runs past the end of the extra field. That
is the RAR5 extra-record ruling ([`rar.md`](rar.md) §6) applied to ZIP's one optional name
record: one bad record costs that record, not the archive. Only the field of an unflagged
name is consulted, so a flagged name's field is never read or reported. The local header's
copy is not read: the listing comes from the central directory, and the local header's
name is only compared with the central one.

Stdlib from 3.12 acts on the field while reading the central directory: it raises
`BadZipFile` over a field shorter than five bytes (whatever its CRC) or a CRC-matching
one that is not UTF-8, and calls `warnings.warn` on an empty one, outside the
diagnostics system; 3.11 ignores it, and so does 7-Zip. archivey's parser reads the
field itself, so the outcome is the same on every Python version and no warning filter
is touched.

Backslashes are normalised on `name` by origin, not globally: a DOS/FAT-origin entry's `\`
is treated as a path separator and rewritten to `/`; a Unix-origin entry keeps `\` as a
literal filename character. `raw_name` keeps the stored bytes unchanged: the header's,
or the Unicode Path field's when that named the member.

**Metadata mapping.** The parser hands the reader the central-directory fields
(`CentralEntry`, ZIP64 values already resolved) and the raw `extra` blob; the reader
classifies symlinks, parses the NTFS, Unix and Extended Timestamp extras into
datetime fields, reads the owner ids from the Unix extras, and keeps the fields it
took nothing from in `extra["zip.extra_fields"]`.

| `ArchiveMember` field | Source | Absent when |
| --- | --- | --- |
| `name` | CDH name bytes, decoded as above, or the CDH Unicode Path field's name; `\` → `/` only for DOS/FAT-origin entries | — |
| `raw_name` | The stored bytes `name` was decoded from, verbatim (no backslash rewrite) | — |
| `extra["alternate_raw_name"]` | The CDH name bytes, when the Unicode Path field named the member | No such field, or it does not match |
| `mode` | `external_attr >> 16` | The producer was not Unix-like, or `external_attr` is 0 — then `None`, never a substituted default |
| `uid` / `gid` | The owner extra fields, read in blob order as unzip 6.00 reads them (§6): Info-ZIP new Unix `0x7875` (version 1, ids of 2, 4 or 8 bytes; Info-ZIP `zip` writes it on Unix by default, 4-byte ids) and Info-ZIP Unix type 2 `0x7855` (16-bit ids) each decide the owner afresh, readable or not, and a `0x7855` after a `0x7875` is ignored; PKWARE Unix `0x000d` and Info-ZIP old Unix `0x5855` (16-bit ids after two times) count only when neither came before, and then the last one that holds ids wins. The two 16-bit Info-ZIP fields hold the ids in local headers only (their central copies are empty, or the two times alone), so a file read finds them only from writers that copy the ids into the directory. A forward pass yields no owner, and takes the directory's at the end | No such field, or the deciding field holds none. A `0x7875` of another version and the central short copies are silent; a deciding field that is cut short, too short for its ids, a `0x7855` neither empty nor exactly 4 bytes, or a `0x7875` with an id size unzip does not read gives none and is dropped with `MEMBER_HEADER_RECORD_SKIPPED` (`record="unix_owner"`, `record_id` the tag). Writers on Windows store none |
| `uname` / `gname` | — | Always: no ZIP extra field records owner names |
| `modified` / `accessed` / `created` / `ctime` | CDH DOS date-time (naive local, 2-second granularity) ← NTFS extra `0x000A` (UTC) ← PKWARE Unix `0x000d` or Info-ZIP old Unix `0x5855` (access and modification time, UTC) ← Extended Timestamp `0x5455` (UTC), later overriding earlier — parsed by archivey. macOS `ditto` and Archive Utility write only `0x5855`, so without it a macOS archive would list naive local DOS times. The Unix fields' times follow unzip 6.00 (measured with `unzip` on crafted local headers, `test_unix_field_times_match_unzip`): ignored in a blob that holds a `0x5455` with data, or after a `0x7855` or `0x7875` (the rule their ids follow), the last one long enough for both times winning. They sit above NTFS, which unzip on Unix does not read. The two "creation" slots (NTFS FILETIME, Extended Timestamp third time; the latter wins) mean what the writer's host says, not what the field says: on Linux and macOS, 7-Zip and p7zip fill the NTFS one from `st_ctime` and libarchive the UT one; Info-ZIP and `ditto` there store no creation time. From a FAT / OS2 / NTFS / VFAT host ("version made by", `_ZIP_BIRTH_TIME_HOSTS`) the time is a birth time and is `created`; from any other host, unknown included, it goes to `ctime` and `created` is `None`. libarchive on Windows stamps host 3 but stores the birth time, so its time lands in `ctime` too. Info-ZIP on Windows puts its birth time in the UT field of the local header only, which listing does not read. Per-writer measurements: [`writer-timestamp-slots.md`](../investigations/writer-timestamp-slots.md) | 1980 sentinel, or every layer invalid. Each invalid layer emits `MEMBER_TIMESTAMP_INVALID`, whose `field` is the member attribute that layer would have filled (the creation slot `created` or `ctime` by the same host rule), even when a lower layer still fills it |
| `type` | Symlink via the `FILE_ATTRIBUTE_REPARSE_POINT` bit in the low word of `external_attr` — provisionally, until the member's data confirms it (§2.2.1) — or via Unix mode bits in its high word; directory when the decoded name (the one `name` comes from) ends in `/`, or in `\` for a DOS/FAT-origin entry, the same on every host OS and Python version. A directory whose `file_size` is not 0 (APPNOTE 4.3.8 forbids it; unzip, 7-Zip and bsdtar create the directory and drop the bytes silently, Go's `archive/zip` refuses the archive) stays a directory, emits `MEMBER_DIRECTORY_DATA_IGNORED` with both sizes, carries the central-directory CRC-32 in `hashes` (unless that field is 0, the conventional value for a directory: then no digest, and `DIGEST_UNVERIFIABLE` where the CRC was the only check, unencrypted or ZipCrypto; a WinZip AES entry, AE-1 with a zeroed field or AE-2 which stores 0 by design, is checked by its HMAC instead and reports nothing), and reads through `open()` like a file: `_open_with_link_follow` refuses a directory by data and a tombstone or special entry by type. `file_size == 0` over a body larger than an empty one of its method (`_ZIP_EMPTY_BODY_SIZES`: 0 stored, 2 deflated, 14 bzip2, 19 LZMA) is reported too and not readable; `compress_size == 2` deflated is the Java `jar` tool's empty body and is silent. A Unix FIFO, device or socket mode makes `OTHER` only when `file_size` is 0; with data it is a `FILE` (`zip -FI` stores a named pipe's content so, and unzip, 7-Zip, bsdtar and `zipfile` all write it as a file) with `MEMBER_SPECIAL_FILE_HAS_DATA`, DR-25. Both carry `extra["special_file_type"]` | — |
| `link_target` | The member's **data**, not its metadata — a bare path for a Unix symlink, a `REPARSE_DATA_BUFFER` for a Windows one (§2.2.1) | The member is encrypted and no password is available, so there is nothing to read it from — `SYMLINK_TARGET_UNAVAILABLE(reason="password_required")`, whose context carries the member's identity and the reason and nothing out of the member; or the ZipCrypto data failed its integrity check under a password only the check byte vouched for, with `reason="password_or_damage"`; or the data failed its CRC, HMAC or decompressor under a password that was not in doubt (or no password at all), with `reason="target_data_damaged"` and the damage raised when the link is opened or extracted; or the writer stored no usable reparse data, with `reason="reparse_data_absent"` (no data at all), `"reparse_data_unrecognized"` (data that is not a link buffer — a file-shaped member is re-typed to carry it, a directory-shaped one stays a targetless link, §2.2.1) or `"reparse_data_nameless"` (a link buffer that parsed but named no target) |
| `compression` | CDH method → `CompressionMethod` | — |
| `is_encrypted` | general-purpose bit 0, or method 99: a WinZip AES member is encrypted even when a writer left bit 0 clear, and opening it asks for a password either way, so listing and opening share one predicate | — |
| `hashes["crc32"]` | CDH CRC, as four big-endian bytes — present for AE-1 (and verified on read); omitted for AE-2, where the format zeroes the field and the HMAC is the integrity signal | WinZip AE-2 members |
| `comment` | CDH member comment. APPNOTE puts it under the name's bit 11, so it decodes as the name would: UTF-8 when flagged over a valid UTF-8 name; unflagged, or flagged over a name that is not UTF-8, the same sniff as an unflagged name (UTF-8 if valid, else `encoding=`, else `zip_unflagged_fallback_encoding`), with no diagnostic; a byte the codec leaves undefined survives as a lone surrogate. `ArchiveInfo.comment` (the end record's, which has no flag) decodes as an unflagged one | The entry stores none, which is the common case |
| `create_system` | CDH "version made by", high byte | Never — an unrecognised value maps to `CreateSystem.UNKNOWN` rather than to nothing |
| `extra` | `zip.compress_type`, plus `zip.aes_vendor_version` / `zip.aes_strength` / `zip.aes_actual_method` on AE members, plus `is_reparse_point` from the attribute bit and `is_junction` when a stored reparse buffer says so (§2.2.1) | — |
| `extra["zip.extra_fields"]` | The CDH extra fields archivey took no value from, as `{tag: data}`: unknown tags (a vendor's field, the `jar` tool's empty `0xcafe`) and known ones that gave nothing (malformed, a Unicode Path field about another name, a `0x5855` under a `0x5455`). The first of several fields with one tag; a tag any copy of which gave a value is left out, and so are the structural ZIP64, WinZip AES and Strong Encryption fields. Charged to `max_metadata_bytes` at 72 bytes per field plus its data (an empty field retains about 70 bytes). A pipe pass sets it when it reaches the directory | Every field gave a value, or there are none: the usual case |

Raw extra-field blobs are not surfaced. Duplicate names are legal and are not merged:
members keep a positional `member_id`, name lookup is last-wins, and currency is computed
in the reader spine so ZIP behaves like every other format.

#### 2.2.1 Windows reparse points, and the junction that cannot be found

A Windows symlink and an NTFS junction are both *reparse points*, and the attribute that
marks them — `FILE_ATTRIBUTE_REPARSE_POINT`, `0x400` in the low (DOS) word of
`external_attr` — is the same for both. What tells them apart is the **reparse tag**
(`IO_REPARSE_TAG_MOUNT_POINT` `0xA0000003` against `IO_REPARSE_TAG_SYMLINK`
`0xA000000C`), and that tag is not in any header: it is the first field of the
`REPARSE_DATA_BUFFER`, which archivers store as the member's *content*. So archivey
reads it exactly where it already reads a symlink target, in `_ensure_link_target`,
and `extra["is_junction"]` is a listing-time flag whose source is read-time data. The 7z
backend has the same problem for the same reason and shares the whole path: the parsing
in `archivey.internal.windows_reparse`, and in `BaseArchiveReader` the capped read
(`_link_target_from_data`), the settle of a reparse point stored with no data
(`_settle_empty_reparse_point`) and the report when encryption blocks the read. Each
backend supplies only which members are reparse points and what type one reverts to.
A pass (`stream_members()`) under `read_link_targets` reads each symlink's data when it
reaches the member, before yielding it, so a reparse point whose data is not a link
buffer is yielded as a file with its content; a streaming pass used to decide only when
it ended, after yielding the member as a link with no stream. Under
`read_link_targets=False` such a link reads `member_state_final=False` until its data
is read.

Two things follow, both measured against archives built on a Windows runner
(`tests/fixtures/external/README.md` §`junction/`, and `tests/test_windows_reparse.py`):

**7-Zip's `-snl` is the only common writer that records any of this**, and it records it
only for a **file** reparse point. bsdtar follows a junction outright and rewrites
Windows symlinks as Unix ones; `Compress-Archive` follows everything.

**A junction is always a directory reparse point, and 7-Zip stores no data for those.**
`junction_dir` and `symlink_dir` come out byte-identical: the reparse bit set, the
directory bit set, zero bytes of content, no extra field carrying the buffer. The tag is
simply not in the archive, so `is_junction` is not derivable from one, and 7-Zip cannot
restore the junction from its own output either. Such a member is surfaced as a symlink
with `link_target` unset and a `SYMLINK_TARGET_UNAVAILABLE(reason="reparse_data_absent")`
diagnostic — a link whose target the writer discarded, rather than a plain directory,
which is what it was before and which silently lost the fact that anything was there.
The trailing `/` the writer stores on that entry is dropped by `normalize_member_name`,
because the member is not a directory; the same member has always been spelled without
it in 7z. The ZIP backend passes `link_stored_as_directory` to
`emit_member_name_normalized` so that drop does not report a `MEMBER_NAME_NORMALIZED`
anomaly — the flag is explicit precisely because the suppression rules are shared, and a
TAR `SYMTYPE` entry named `link/` *is* an anomaly worth reporting.

`extra["is_reparse_point"]` records the bit itself, so it is available while listing and
stays set even when the data turns out not to be a link buffer and the member is
re-typed. `extra["is_junction"]` is the stronger claim and needs the tag out of the data,
which is why a 7-Zip-written junction carries the first key and not the second.

**The attribute bit is a candidate, not a verdict; the data decides.** `0x400` says the
entry was a reparse point on the source filesystem. It does not say the archive carries
the buffer, and it does not say the tag named a link — Windows sets the same bit for
deduplication stubs, cloud placeholders and WSL entries, whose data is ordinary content.
So a **file-shaped** member whose data is present but does not parse as a link buffer is
put back to the type it would otherwise have had and keeps that content, with
`SYMLINK_TARGET_UNAVAILABLE(reason="reparse_data_unrecognized")` recording the
reinterpretation. Presenting it the other way round would cost the caller a readable
member on the strength of a bit, and `open()` on it would raise.

Two members are left a link with no target. One has *no* data, so there is nothing to
reinterpret. The other is **directory-shaped** — stored with the trailing slash — where
the re-type would buy nothing: `open()` refuses a directory, so the data the re-type
exists to preserve stays unreachable regardless, and the entry has already lost its
trailing slash to name normalization (whose diagnostic the backend suppresses for a link
stored with the directory convention, §2.2.1). It keeps the `reparse_data_unrecognized`
reason, since that is still what happened; only the outcome differs.

**What the target looks like, and what extraction does with it.** The buffer holds a
Windows path, so the parser normalizes it (`normalize_windows_link_target`, shared with
7z and with RAR5 redirect types 2 and 3): every `\` becomes `/`, a leading `\??\` or
`\\?\` is dropped, and `UNC\` after it becomes `//`. So `\??\C:\Windows` lists as
`C:/Windows`, `\??\UNC\srv\share` as `//srv/share` and `..\up\x` as `../up/x`, in
all three formats. Extraction refuses a symlink target with a drive letter or a UNC root
at every policy and on every OS (maintainer ruling 2026-10-06, `safe-extraction`): on
POSIX `C:/Windows` would otherwise be created as a relative link into a directory named
`C:`, and on Windows the same archive is refused, so one archive would extract two ways.
`STRICT` and `STANDARD` also refuse a `:` or a Windows-reserved device name in a target
segment (`file:stream`, `sub/NUL`), as they do in a member name.
`tests/test_link_target_portability.py` pins each case in ZIP, 7z and RAR5.

The junction path in the reader is therefore correct and unreachable from any archive
these tools produce. It is kept, and tested against an assembled buffer, because the
format permits a writer to store one and the `archive-data-model` spec promises the
flag; `tests/test_windows_reparse.py::test_the_junction_and_the_symlink_are_indistinguishable_on_disk`
pins the measurement so a 7-Zip that changes its mind fails rather than passes quietly.

**A symlink's digest covers the bytes the link is stored as.** ZIP stores a link's target
as the member's data, so `hashes["crc32"]` is a real digest — of a *path* for a Unix
symlink (9 bytes for `file1.txt`), and of the `REPARSE_DATA_BUFFER` for a Windows one
(§2.2.1), which is a Win32 structure rather than the path inside it. Either way it is not
a digest of whatever the link resolves to. Kept, because the value is genuine and 7z and RAR3/4 record
exactly the same thing; said out loud, because `hashes` otherwise reads as being about
content, and a caller de-duplicating by digest or checking content without decompressing
would take it that way. RAR5 is the instructive contrast: it stores links as header
redirects with no data stream, so its CRC32 field covers zero bytes and archivey surfaces
no digest at all rather than the constant `crc32(b"")` —
[`formats/rar.md`](rar.md) §2.2.

**The end record is checked against the directory the walk read.** The walk reads
directory entries until it has consumed the size the end record gives. After the last
member, the reader emits the walk's findings as `ARCHIVE_EOF_MARKER_MISSING`: an entry
count that differs from the entries read (the ZIP64 record's count when there is one; a
classic count modulo 65536, because old 7-Zip versions stored a larger count's low 16
bits without ZIP64 and current 7-Zip's `ZipIn.cpp` accepts that with a "16-bit
overflow" note), an archive comment length past the end of the file, and an entry whose
name, extra field or comment runs past the directory, which is cut at the directory's
end. Info-ZIP unzip warns or errors on each and 7-Zip says "Headers Error", after
testing every member, so the default policy lists and reads; `STRICT` refuses. The
end record is the last signature in the tail, so a decoy end-record signature inside
the archive comment passes the checks: the archive lists as empty (`EMPTY_ARCHIVE`), as
with Info-ZIP unzip, where 7-Zip searches further back and lists the real members.

#### 2.2.2 From a pipe: the forward pass

A non-seekable source under `streaming=True` is read by `ZipForwardReader`
(`internal/backends/zip_forward_reader.py`) over `LocalHeaderWalk`
(`internal/backends/zip_local_walk.py`), which parses each local header and each central
header with the parser's own code and reads the source forward only. `streaming=False`
on a pipe raises `StreamNotSeekableError` naming `streaming=True`. Nothing is spooled
(ADR [0010](../decisions/0010-no-silent-buffer-nonseekable.md)). The receipt is
`REQUIRES_SCANNING`, `DIRECT`, `FORWARD_ONLY`; `member_count` and the archive comment
are `None`, and `members_report_if_available()` returns `None`, until the directory has
been read. The full design, row by row, is the `native-zip-reader` change's `design.md`
(§"Streaming: a forward walk over local headers").

**Where a member's data ends.** A sized local header gives it. With bit 3 set the walk
decodes to the codec's end marker (DEFLATE, Deflate64, bzip2, Zstandard, LZMA with the
EOS bit, PPMd with its end mark), then finds the data descriptor by the decoded CRC; a
STORED member ends at a descriptor whose CRC and size match the bytes before it and that
a ZIP signature follows, signed or not. An encrypted bit-3 member nobody reads is passed
the same way on its ciphertext, matched on the size, so no key is needed. A bit-3 LZMA
member without EOS has no end its codec can find, and its read is
`UnsupportedFeatureError`. PPMd has no flag for a missing end mark: such a member fails
its decode, as from a file. A bit-3 codec member whose decode fails or finds no end is
passed by the same scan on its stored bytes. The scan starts at the bytes the walk kept
of what the decode read (`_CODEC_RETAIN`, 4 MiB) and counts the ones before them into the
size it matches, so the descriptor is found unless the decode read more than that past
the data's end; then the scan runs to the end of the stream and the pass ends with its
error. A bit-3 PPMd member is always decoded in a child process
(`max_ppmd_in_process_input=None` refuses it with `ResourceLimitError`). Several password
candidates are confirmed on the first 1 MiB, kept and replayed (§2.3).

**What the pass yields, and what changes at the end.** Each member is yielded with what
its local header gives: `mode`, `create_system` and `comment` are `None`, the type is
`FILE` or `DIRECTORY` from the name (a trailing `\` is a `DIRECTORY` until the host is
known), a `\` inside a name is a literal character, and `member_state_final` is `False`.
`uid` and `gid` are `None` as well, though a local header can carry them (§6). After the
last member the pass keeps what follows (bounded by the listing limits, see
`_tail_bound`) and parses it with `find_end_record` and `CentralDirectoryWalk`, so it
picks the end record and reads the directory exactly as a file read does. Each member is
then updated in place from the directory entry at its offset: type, name (a DOS host
makes `\` a separator), mode, host, comment, times and owner ids from the directory's
extra fields, and `is_current`, which follows the directory order. Member ids and listing
order stay the file's. A local entry no directory entry points to is dropped and reported
as `ARCHIVE_UNLISTED_DATA`. A reparse-flagged member whose data is not a reparse buffer
stays a file. `max_members` is charged on the local headers as they are yielded and again
on the directory's entries, whose text is weighed against `max_metadata_bytes` as it
arrives. The archive comment is weighed once the end record is read.

**What the pass keeps for link targets.** A symlink's target is its data, and only the
directory says which members are links, so the pass keeps the data of every member short
enough to be one (`MAX_LINK_TARGET_BYTES`, 4096 bytes decoded), the ciphertext of an
unread encrypted one (8 KiB), and a Windows reparse buffer's start of longer data
(§2.2.1). This is files' data, which a file read never keeps, so it is not charged to the
listing limits: an archive of 20 000 files of 4 KB lists from a pipe as from a file. It
is bounded on its own (`ZipForwardReader._hold`): the pass holds at most
`max_metadata_bytes` of it, on top of the listing, and drops the longest first, since a
link target is a path and shorter than most files. At the directory it keeps only the
links' data. A link whose data was dropped raises `ResourceLimitError` at the end of the
pass.

**Extraction** writes each member as it arrives and settles at the end of the pass
(`ExtractionCoordinator._settle_revised_members`): modes and owners applied, a member the
directory makes a symlink turned into a link after the `filter` and policy run again on
it, what a member the directory makes `OTHER` wrote removed, a renamed member moved, and
the selection made again on what each member became (`design.md`, "The selection is made
again"). A caller's entry the pass replaces is moved aside and put back unless the member
still lands there.

**Failures found at the end of the pass** (`_failed_at_end`,
`_settle_failures_at_end`): a data descriptor the directory contradicts, a local header
whose CRC, sizes or encryption flag the directory contradicts (not a central CRC-32 of
0 over data against a local value, §5), a same-name pair whose directory order reverses the
file order, and a directory entry with no local entry. Each fails its member
(`CorruptionError`; the reversed pair only updates the members in `stream_members()`),
and extraction removes what it wrote for it. A directory entry pointing at a local
header an earlier entry took ends the pass with `CorruptionError`. Two headers storing
different names fail nothing: the member takes the directory's name, and a file or link
reports `MEMBER_LOCAL_NAME_DIFFERS` as a file read does when it opens the member (§5);
extraction moves what it wrote.

**Where the pass and a file read differ**, each pinned in
`tests/test_zip_pipe_parity.py` (`_DIFFERS`) or the tests named in §8. The first three
are shapes only crafted archives have (DR-5a); the rest come from damage, or from the
directory's refusals the file read is to drop:

- A data descriptor that understates a STORED member, and the reversed same-name pair,
  fail a member the file read writes.
- Junk between a bit-3 codec member's end and its descriptor counts into the member,
  where the file read refuses it (DR-3).
- The first of several inconclusive password candidates wins (§6).
- Under `on_error="stop"`, a member whose data fails as the pass writes it stops the run,
  though the directory may then not list it. `abort_on` fires before the end of the pass
  decides a member's selection, and the bomb limits count what the pass wrote until it
  is taken back.
- A bit-3 codec member whose data is damaged can fail with another error than from a
  file (`TruncatedError` where the file read says `DataAfterEndError`, for one): the
  file read knows its stored size, and the pass finds its end by the scan for its
  descriptor (§"Where a member's data ends"). The members after it read the same,
  unless its decode read more than 4 MiB past the data's end: then the pass ends there.
- A bit-3 LZMA member without the EOS bit is refused (`UnsupportedFeatureError`); a
  file read knows its size and reads it.
- A link member the pass wrote as a file keeps the error its data read gave: under no
  password an encrypted link fails with `EncryptionError`, where the file read reports
  `LinkTargetNotFoundError`.
- A symlink whose target is behind `max_metadata_bytes` of short members' data no
  shorter than it ends the pass with `ResourceLimitError`; the file read lists it
  (§"What the pass keeps for link targets").
- A damaged directory lists the directory's entries before the damage and the local
  headers' past it in both reads, then raises; the two write the same files. The pass
  applies the directory to the members it describes before the damage and keeps the
  rest as their local headers say, without reporting them as unlisted.
- Damaged ZIP64 end records no longer refuse the archive in either read. When no
  field of the classic end record holds the ZIP64 sentinel and the directory's end can
  be placed, the classic record is used and `ARCHIVE_EOF_MARKER_MISSING` (marker
  `zip64_end_of_central_directory`, `observed_kind="absent"`) reports it. Otherwise
  the end records cannot be used: the file read walks the local headers, lists what
  they hold and raises the end records' error, as the pass does. A locator naming an
  offset among the members' data reads the same, though the two say why in different
  words: a file read checks the bytes there, the pass has passed them. One naming
  bytes the pass's listing limits dropped is a `ResourceLimitError` from a pipe, since
  higher limits keep those bytes.

### 2.3 Member data

**`zipfile`'s own decoders were never used** — meaning `ZipExtFile`, not the standard library:
the codec layer still ends at `zlib`, `bz2` and `lzma` for the methods they cover. archivey
locates the member's raw compressed bytes with a bounded LFH parse (the fixed 30 bytes
plus the local name and extra lengths, with an absurd data-offset cap and a name
comparison with the CDH that reports a mismatch), slices the source, and dispatches on
the ZIP method id to the shared codec layer.

The reason is coverage and uniformity, in that order. `ZipExtFile` handles STORED, DEFLATE,
BZIP2 and LZMA; the codec layer adds Deflate64 (9), Zstd (93) and PPMd (98), which the
registry already advertised for ZIP. It also puts ZIP member reads on the same
`VerifyingStream` and the same exception translation as every other backend, so a corrupt
body raises `CorruptionError` and a cut-short one raises `TruncatedError` through shared
code — and it is what lets a ZIP member use the accelerators when the caller turns them on,
since `use_rapidgzip` covers raw deflate.

**Codec settings 7-Zip names but this reader cannot decode are unsupported, not
corrupt.** An LZMA member's properties byte with `lc + lp` over 4 (7-Zip accepts up to
12 and writes it with `-mm=LZMA:lc=8`; liblzma stops at 4) raises
`UnsupportedFeatureError` through `decode_lzma_filter_properties`, the check the 7z
pipeline uses for the same byte; properties it cannot read at all (a byte past 224, a
blob of the wrong length) are `CorruptionError` from the same function. A PPMd member's
restore method 2 is `UnsupportedFeatureError` and 3 to 15 `CorruptionError`, as 7-Zip
reports "Unsupported Method" and "Data Error". Each of these errors names the member.
Under ZipCrypto those settings are decrypted with a key one byte vouched for, so both
stay `CorruptionError` there and count as the candidate failing.

A member's compressed data is one stream of its codec and nothing else, as 7-Zip,
Info-ZIP and `zipfile` read it: each codec ends the member at its stream's end (an LZMA
member at its end marker; `lzma.LZMAFile` would start a second raw stream on the bytes
after it and read that as content). The reader opens every member with
`StreamConfig.refuse_input_after_end`, so any byte of the compressed data the codec
leaves after that end, a zero too, and a second stream, are `DataAfterEndError` (a
`CorruptionError`), raised once the output before them has been read, whatever the
declared size and CRC cover. 7-Zip 23.01 `7z t` fails every such member ("Data Error",
or "There are some data after the end of the payload data"), and the bytes may be a side
channel (`design-rules.md` DR-3). Under ZipCrypto the read raises the `EncryptionError`
of an unconfirmed password instead, caused by that `CorruptionError`, as the verify
table below records. A Zstd member (method 93) is one frame: a second frame, a skippable
one too, is refused, where a 7z Zstd coder reads concatenated frames as one stream. An
LZMA member with bit 1 clear, and a PPMd member, end at their declared size, and their
input must end there too: right after it for LZMA (or one zero byte later, which 7-Zip's
encoder sometimes writes), after the end mark PPMd8 must carry. So a declared size short
of the stream's data is `CorruptionError`, not `TruncatedError`: the input is too long,
not short. The 7z handbook (§ coders, *Input after the stream*) has how each codec finds
its end, and what it cannot see; `inflate64` drops input after a Deflate64 stream's end
and has no `unused_data`, so `Deflate64Decoder` holds back the last byte it is given,
and input follows the end when `eof` is already True as that byte goes in. The
accelerators read on into a second stream; at the bytes after the first they hand the
read over to the standard library, which refuses them, so the verdict does not depend on
the accelerator. Finding where a DEFLATE member's first stream ends takes a zlib decode
from its start (a rapidgzip resume point may lie in a second stream), so a full read
under rapidgzip pays that decode too; the accelerator still speeds up seeks.

Encrypted members take the same route with a decrypt stage between the slice and the codec
layer, so they decode every method an unencrypted member does, and their CRC runs through
the same fused verifier:

| | Decrypt stage | Notes |
| --- | --- | --- |
| Traditional ZipCrypto | archivey (`zipcrypto.py`, `ZipCryptoDecryptStream`) | One-byte verifier, so ~1 in 256 wrong passwords passes it. Pure Python, byte by byte, at the speed of stdlib's own decrypter (~1.6 MiB/s here). The cipher state depends on every earlier byte, so a backward seek restarts from the saved post-header state and a forward seek decrypts what it skips; `AUTO` accelerators stay off over it, because they read out of order. A seek back, or a forward seek in a compressed member (whose next read decodes the gap), keeps the CRC; a forward seek in a STORED member jumps, and a read past it gives the CRC up. The CRC is ZipCrypto's only content check: after that, the rest of the member is checked for length only (ADR 0014). A 12-byte header the file cuts short is `TruncatedError` on every password path |
| WinZip AES (method 99, extra `0x9901`) | archivey (`zip_aes.py`) | AES-128, -192 or -256, as the extra's strength byte says · PBKDF2-HMAC-SHA1 at 1 000 iterations · AES-CTR · HMAC-SHA1 truncated to 10 bytes |
| PKWARE Strong Encryption (APPNOTE §7: bit 6, or extra `0x0017`) | refused | Listed as encrypted; opening it raises `UnsupportedFeatureError` naming Strong Encryption, with or without a password. A symlink of this kind lists with `link_target` unset (`target_data_encrypted`). When the central directory itself is encrypted (bit 13), it cannot be listed; an archive extra data record (`PK\x06\x08`) where the directory should start makes the open the same refusal instead of a `CorruptionError` from the listing. Best-effort: without that record the archive reads as corrupt |

Both cheap key checks admit some wrong passwords (ZipCrypto 2⁻⁸, WinZip AES 2⁻¹⁶), so
both schemes share one confirmation ladder (`password_confirm.py`). With **one** possible
password it is accepted on the cheap check, and the CRC or HMAC at EOF is the real test.
With **several**, each surviving candidate runs a bounded confirm before one is accepted:
for a DEFLATE, Deflate64, bzip2, LZMA or Zstandard member the decompressor rejects a wrong
key within a few bytes, so a bounded prefix decode is enough (`REJECTING_CODECS` in
`password_confirm.py`, re-measured by the tests). PPMd is not a measured rejecter, so a
PPMd member is decoded to its CRC once per surviving candidate. A WinZip AES member whose
CRC is out of reach (AE-2 has none) is read to its end instead when it fits the budget or
has no rejecting codec: the HMAC covers the whole member. A **STORED** ZipCrypto member
has no decompressor, so the only discriminator is the whole-stream CRC — all surviving
candidates are resolved in one shared ciphertext pass computing each candidate's CRC in
constant memory, earliest match winning. That cost is irreducible for the format.

From a pipe the data goes by once, so there is no second walk. The pass keeps the first
`PASSWORD_CONFIRM_MAX_INPUT_BYTES` (1 MiB) of the member's data and runs each candidate's
bounded confirm on it; the winner reads those bytes, then the rest of the stream. A bit-3
member is confirmed only when it ends inside them, by its descriptor's CRC or the HMAC.
When several candidates are still inconclusive, the first wins, and its CRC or HMAC at
the end decides, as for one password (§6). The pass keeps the ciphertext of an unread
encrypted member short enough to be a link (8 KiB) and decrypts it only if the directory
makes it one. A member whose local header states a plaintext size that fits a link but
whose ciphertext is longer (only a padded codec stream) is decoded as the pass drains it
instead, only under `read_link_targets` (its default): that reads a target nobody asked
for, and may ask a password provider about a member that turns out to be no link. A
bit-3 member's sizes come after its data, so one that long is not kept or decoded, and
its target is lost where the file read has it (tracked for stage 3.4).

With one ZipCrypto password, a wrong password that passes the check byte fails where the
decrypted bytes first meet a check. `open()` parses an LZMA or PPMd member's codec
header, the first bytes decrypted, so an LZMA member usually fails there, and a PPMd
member does when its two-byte header decrypts to parameters out of range. Otherwise it
is the first read or forward seek the decoder objects to, and for a STORED member the
CRC at the end. Each raises the same `EncryptionError` that names both a wrong password
and damage (§5).

**A read that stops before the check is reported.** When nothing stronger than the cheap
check accepted the password (one password), or a confirm used up its budget without
reaching a CRC or HMAC, the member stream watches for the caller stopping short. Closing it
after reading some bytes and before the end emits `ENCRYPTED_MEMBER_UNVERIFIED`: those
bytes may have been decrypted with a wrong password. `check` says which of the two
accepted it (`weak_open_check`, `confirm_budget_exhausted`); `reason` is `partial_read`,
or `seek` for a ZipCrypto member whose seek jumped past its read position and so lost
its CRC (a STORED member's forward seek), even if it then read to the end. A WinZip AES
seek keeps the HMAC, so there only a read that reaches the end counts. Extraction reads
every member to the end and never emits it, and a candidate that a CRC confirmed has
nothing to report.

For AES, a wrong password fails fast on the 2-byte verification value with no bytes
returned; a tampered ciphertext fails on the HMAC at the terminal read, as
`CorruptionError` whether one password or several were tried. Every candidate that
reached the HMAC had passed the 16-bit check, so damage is the likelier cause. A partial
read then `close()` is quiet — HMAC is a content verdict, not teardown
([ADR 0014](../decisions/0014-integrity-verdicts-from-reads-not-close.md)). AE-1
surfaces and verifies `crc32` alongside the HMAC; AE-2 surfaces neither. Under
`seekable_members=True` an AES member seeks by restarting CTR at the target's block. A
seek that jumps gives up the AE-1 CRC once a read passes it, but never the HMAC: the
read that returns the last byte reads the ciphertext the seeks skipped (no decryption)
and completes the HMAC.

An unknown method id lists fine and raises `UnsupportedFeatureError` on read — never
guessed output. A missing optional package raises `PackageNotInstalledError` naming the
extra.

### 2.4 Extract

Nothing here is ZIP-specific. Path traversal, symlink escape, name collisions,
cross-platform name safety and the byte/ratio/member caps are all the shared extraction
spine — see [`safe-extraction`](../../openspec/specs/safe-extraction/spec.md) and
[`threat-model.md`](../threat-model.md). A blocked member does not end the run; the rest of
the archive still extracts.

### 2.5 Write

Not shipped, and not ZIP-specific: no format has a writer. `format-zip` used to specify
streaming write via data descriptors and no longer does — the requirement went in
`2026-09-02-drop-unshipped-write-claims`, to be restored with the writing phase rather
than edited around. The format-level fact it rested on survives in §1: a ZIP records late
sizes in a DD, which is why bit 3 exists at all.

## 3. In the wild

**ZIP is the container other formats are built on.** JARs, wheels, APKs, zipapps, OOXML
and the ODF family are all ZIPs, and `open_archive` reads them as such with nothing to
distinguish them from a backup. A census of 643 readable ZIPs on one Linux image found 363
JARs (`META-INF/MANIFEST.MF`), 176 ODF-family (a `mimetype` entry stored first, 176/176,
none compressed), 11 wheels, and 91 with no marker at all — of which 85 were named `.zip`
and none was user data. Recognising the role is post-1.0 and, if built, must report "not
recognised" rather than "this is data"; the idea is in [`IDEAS.md`](../IDEAS.md)
§Detection. For ZIP the cheap tests are nearly free, because the first entry's name and
its `compress_type` are both already in the central directory.

**Prefixed ZIPs are an idiom, not an abuse** — `zipapp`, pex, shiv, Spring Boot executable
JARs, self-extracting installers, appended-ZIP polyglots. Two write conventions exist and
store different numbers:

| | Stored offsets count from | EOCD adjustment |
| --- | --- | --- |
| Written in place (`zipapp`: open one file, emit a stub, write entries through the same handle) | Byte 0 of the file, stub included | 0 |
| Concatenated (`cat stub payload.zip`) | The start of the ZIP data | The prefix length |

The difference is self-correcting: the EOCD's own position is known once it is found, so
`(eocd_pos - size_cd) - offset_cd` recovers the base under either convention and every
entry offset is read through it. This is why `payload_offset` is defined as the absolute
position of the earliest local file header rather than as the adjustment — the adjustment
is 0 for `zipapp`, which would report the motivating case as unprefixed. An empty archive
has no local header to point at, so there `payload_offset` is the EOCD-derived base.

**Producers disagree about encodings.** Info-ZIP and others write valid UTF-8 names without
setting bit 11, which is the case the sniff exists for; `tests/fixtures/external/encoding_infozip_jules.zip`
is a real sample.

**Producers disagree about split naming, and the disagreement is not cosmetic.** Info-ZIP
and WinZip write `name.z01 … name.zip` (and `name.z100+` once the set exceeds 99 parts);
7-Zip writes `name.zip.001 … name.zip.00N`. The two look alike and are different things:

| Family | Made by | Structure | Answer |
| --- | --- | --- | --- |
| `name.z01 … name.zip` | `zip -s` | true spanned set; entries addressed by (disk, offset), a `PK\x07\x08` spanning marker at the head of disk 1 | refuse |
| `name.zip.001 …` | `7z -v` | raw byte slices of one finished single-disk ZIP | join |

7-Zip's `-v` is a splitter, not an archive feature: it slices the finished output at a
fixed size with no awareness of what falls where, which is why the parts concatenate back
to the original and why the rejoined EOCD reads disk `(0, 0)`. That is the same thing it
does to `.7z`, so ZIP and 7z get the same answer for the same input.

Info-ZIP's do not, and a joined spanned set fails in a shape worth knowing: it **lists
correctly and then reads nothing.** The central directory survives intact on the final
disk, so enumeration works — but every entry's offset is relative to *its own* disk,
while the EOCD's `offset_cd` is relative to the last one. Reading a three-disk
`zip -s 64k` set joined by hand:

```
disk sizes [65536, 65536, 19117]      EOCD at 150167, size_cd=75, offset_cd=19020
stdlib's adjustment = eocd - size_cd - offset_cd = 131072   (= the two preceding disks)
first entry's header_offset after it = 131076 -> bytes there are b'\xaa\xff\x82\x12'
lists: ['p.bin']          read p.bin: BadZipFile: Bad magic number for file header
```

That constant is applied to every entry, and it is correct for exactly one disk — the
last. So a member whose local header sits on the final disk reads normally, and every
member before it lands in the middle of somebody else's data and raises `BadZipFile`.
Adding a small final member to the set above shows both halves at once:

```
big.bin    header_offset=131076   BadZipFile: Bad magic number for file header
last.txt   header_offset=150069   read OK, 17 bytes
```

Which members survive depends on where `zip -s` happened to cut, so a joined set is not
reliably unreadable — it is *partly* readable, which is worse. In sets without a small
tail member there is nothing to survive: 3, 5 and 12-disk sets all listed every member and
read none. The failures are at least loud — a wrong offset misses the `PK\x03\x04` magic
rather than returning plausible bytes. Refusing on the non-zero EOCD disk fields (§5)
refuses a set that cannot be read whole, not one that happened to work.

A possible later refinement for the refuse path — detect first, upgrade a failed detection
to rejoin-first when the name looks volume-shaped — is in [`IDEAS.md`](../IDEAS.md) until
someone takes it up; do not invent a ZIP-only half-step here.

**Producers disagree about encryption defaults.** 7-Zip's `-tzip` default is ZipCrypto and
`-mem=AES256` selects WinZip AES; stdlib `zipfile` writes neither. That is why the
encrypted corpus rows shell out to `7z` and skip silently without it. Reading is uneven
too: Info-ZIP `unzip` 6.00, still the default `unzip` on Debian, cannot read WinZip AES at
all and exits 81 ("unsupported compression method") on any method-99 member — an archive
archivey reads may be one the system CLI cannot.

**And about the AES vendor version.** AE-1 stores the plaintext CRC in the headers; AE-2
zeroes it, because for a very small file the CRC narrows the plaintext independently of the
cipher. 7-Zip writes AE-2, which is the modern default — but `pyzipper`, the
`zipfile` fork that adds WinZip AES to Python, wrote **AE-1 for every member regardless of
size** from 0.3.0 (2019-02) through 0.3.6 (2022-07), and 0.3.6 was the only release
available until 0.4.0 switched to AE-2 on 2026-05-14. Stdlib writes no encryption at all,
so a Python program emitting an AES ZIP in those four years was almost certainly emitting
AE-1. It is not a legacy branch kept for symmetry, and 0.4.0 still emits it under an opt-in
`conditionally_include_crc` for members of at least 20 bytes. `tests/fixtures/external/aes_ae1_pyzipper036.zip`
is a real sample; the CRC is verified on AE-1 and absent on AE-2 (§2.3).

ZIP64 is not exotic. A central directory of 70 000 entries lists correctly, through the
ZIP64 end record or, without one, as a classic count modulo 65 536 (§2.2).

**macOS writes ZIPs past 4 GiB without ZIP64.** Archive Utility (Finder's Compress,
`ditto -c -k`) keeps the classic records and stores every offset modulo 2³², the end
record's offset of the directory included; the producer markers are version made by
Unix 2.1, the old Info-ZIP `UX` extra field (`0x5855`) and data descriptors with
signatures. A backup in the scan had 261 of its 2 122 offsets off by exactly
2³², and 7-Zip and stdlib cannot open it
([`investigations/2026-10-backup-scan.md`](../investigations/2026-10-backup-scan.md)
§4). One rule recovers every offset, and `CentralDirectoryWalk` applies it: the
members lie in directory order, so an offset below the furthest end of the members
before it gets 2³² added until it is not. The wrapped directory offset shows up as a stub length
(`base`) 2³² too large, so the walk takes `base` modulo 2³². Each correction needs a
local header with the entry's name at the corrected offset, so a ZIP under 4 GiB
whose directory is out of file order is untouched (the corrected offset would be past
the directory, and nothing is read there). `ARCHIVE_OFFSET_WRAPPED` records each one.

**What Finder and `ditto` write besides sizes.** davi's tests on macOS (2026-10-16), a
folder with relative, absolute, dangling and directory symlinks, a hard link, an
executable, an xattr, a resource fork, an empty folder and an NFD name:

- Both write only stored directories and deflated files; `ditto
  --zlibCompressionLevel 0` still writes method 8, with stored blocks inside.
- Symlinks are Unix-mode entries whose data is the target; the hard link is a second
  copy. A FIFO hangs `ditto`.
- Names are UTF-8 without the UTF-8 flag (bit 11), NFD as the filesystem gives them,
  so every non-ASCII name reports `MEMBER_NAME_ENCODING_INFERRED`. davi chose to keep
  that diagnostic.
- Finder writes UTC into the DOS time field and `ditto` local time; both write the
  real UTC mtime in the `UX` field (`0x5855`).
- Both write `__MACOSX/<path>/._<name>` AppleDouble sidecars. `ditto` writes one for
  every entry, symlinks included, plus `__MACOSX/` directory entries, and drops the
  top folder unless given `--keepParent`. Finder keeps the top folder with its own
  sidecar and writes none for symlinks. archivey lists sidecars as plain files, like
  `unzip`; `ditto -x` folds them back into xattrs instead. §6 has why there is no
  marking key.

**A member over 4 GiB from macOS.** The same writer stores a member's sizes modulo 2³²
too, in the directory and the data descriptor alike. davi's tests on macOS (2026-10-15):
a Finder archive of five 1 GiB random files reads with the offset corrections; a
10 000 000 000-byte text file lists as 1 410 065 408 (10¹⁰ mod 2³²) and its read stopped
at that size with "Decompressed content exceeds its declared size". The archive records
the real size nowhere: the directory, the local header and the descriptor hold 32 bits,
the `UX` field (`0x5855`) holds only times and, in the local copy, owner ids, deflate
does not record its length, and the `__MACOSX/._name` sidecar is AppleDouble (Finder
info, extended attributes, resource fork; no data length).

- **The fingerprint** (`written_by_archive_utility` in `zip_parser.py`): version made by
  `0x0315` (Unix, 2.1), bit 3 set and bit 0 (encryption) clear (Archive Utility
  writes 16-byte descriptors with their signature, and never encrypts), and the
  central extra field is exactly one `UX` field. The reader also requires a classic
  end record, since the writer never writes ZIP64. The `__MACOSX` sidecars usually
  appear too, but are not required. Anyone can copy these fields, so
  the fingerprint only decides whether to try the corrections below. Each is checked
  against the archive's own bytes, and the CRC-32 is still checked on a read.
- **Compressed size, from the layout.** Only in a directory past 4 GiB can a compressed
  size have wrapped. There `CentralDirectoryWalk` holds each marked entry back until it
  has the next entry's offset, then measures the data from the local header's data
  start to the next local header (or the directory, for the last entry) less the
  16-byte descriptor. A length past the stored size by a positive multiple of 2³² is
  taken. The entry after a marked one looks for its header at its stored offset plus
  each multiple of 2³² in turn (from the first not below the members before it) and
  keeps the first with its name. The walk reads each local header once (its fixed
  part, then its name), and the data start comes from the same read, so the cost is
  one header read per member, only in a marked archive past 4 GiB. A pipe has the data
  starts of the `UX` local headers its pass met, and measures the same way.
- **Size, stored members:** the compressed size, when the two agree modulo 2³².
- **Size, deflated members: deflate's ratio bound.** The real size is the stored size
  plus some multiple of 2³², and the compressed size bounds it from above: a 258-byte
  match takes at least one bit of literal/length code and one of distance code, so a
  byte of deflate decodes to at most 1032 bytes. Nothing bounds it from below: empty
  stored blocks pad a valid stream without bound, so a large stream can decode to a few
  bytes.
  - **Only the stored value fits** (1032 times the compressed size is under the stored
    value plus 2³²): that is the size. Every member under about 4.16 MB compressed
    falls here (1032 × 4.16 MB ≈ 4 GiB), which is most members of a typical archive.
  - **Several fit:** the size is unknown. `size` is `None`, `extra["size_mod_2_32"]`
    holds the stored value, and `MEMBER_SIZE_WRAPPED` carries `size=None`. This covers
    every deflated member over about 4.16 MB compressed, whatever its real size, since
    only a read tells a member over 4 GiB from one under it. A file read decodes with
    no declared size (limits and the expansion ratio stay on); with no declared size,
    `AUTO` keeps rapidgzip off (it needs one to report a cut stream), so such a member
    reads through zlib unless the caller turns the accelerator `ON`. When a read
    reaches the end, the length must equal the stored value modulo 2³² and be at most
    1032 times the compressed size, then the CRC-32 must match, and only then does
    `member.size` take the length (`MemberVerifier`'s `end_check` / `fill_size`;
    `ArchiveMember` is mutable for late-bound fields, as digests are). Until then
    `member_state_final` reads `False` (the end of a `streaming=True` pass, or closing
    the reader, settles it).
    `extra["size_mod_2_32"]` stays after the fill-in, so the member still says the
    archive stored only the low bits. A pipe pass that decoded the member to its end
    with the directory's CRC-32 keeps the size it counted. davi chose this (option A,
    2026-10-15) over decoding such members while listing, which would read a whole
    archive of videos to list it. An entry with no CRC-32 (a directory carrying data)
    has nothing to check a read against, so it keeps the stored size instead of
    `None`; its compressed size, and a stored member's size, still come from the
    layout, which needs no digest.
  - **None fits:** the stored value is over 1032 times the compressed size, a damaged
    entry. The stored size stays and the read fails on it.
- Every member whose sizes were corrected or left unknown gets one `MEMBER_SIZE_WRAPPED`
  (`SizeWrapContext`: stored and corrected compressed size and size), advisory for the
  same reason as `ARCHIVE_OFFSET_WRAPPED`. Its message says the archive passed 4 GiB
  only when a size was corrected; an unknown one says only that the size may be past
  4 GiB. A member under about 4.16 MB compressed is untouched and reports nothing. A
  larger deflated member lists `size=None` and reports one even in an archive under
  4 GiB, since only a read tells.
- Archive Utility writes only stored and deflated members; another method under the
  fingerprint keeps its stored sizes.

## 4. Threat surface

ZIP-specific only. General extraction and name hazards are §2.4.

- **Listing is attacker-controlled work.** A small file can declare an enormous number of
  entries, or entries with enormous names and comments, with nothing decompressed. Capped
  by `ListingLimits`; see [`threat-model.md`](../threat-model.md) O1.
- **ZIP is the canonical bomb.** Declared sizes are attacker-controlled and so is the
  stored CRC, so neither bounds the output; the caps are enforced against bytes actually
  written, plus a live ratio measured from bytes consumed. Nesting is not tracked — a
  zip-of-zips amplifies one level at a time (O6).
- **Overlapping entries** are a distinct crafted shape, caught when a member opens by
  an overlap guard and raised as `CorruptionError`. The guard was stdlib's: each
  entry's data must end before the next local header in file order, or before the
  central directory. The bounds come from the offsets the listing walk records
  (`_DataBounds`, one sorted array), or from one index pass when `member_count` is
  read first (§2.2). Of two entries over one local header, the first in directory order reads
  and the later one is refused. stdlib raised on 3.11, on 3.12 before 3.12.10 and on
  3.13 before 3.13.3; 3.12.10+, 3.13.3+ and 3.14 warn and read both. archivey keeps
  refusing, because many entries over one local header is the overlapping-entry
  amplification shape (DR-9a).
- **Confirming a ZipCrypto password costs time that depends on the archive, and that cost
  is observable**: the one-byte verifier cannot decide between candidates, so a STORED
  member is read through and its CRC compared. Timing the call, or watching how much is
  read, tells an observer that some candidate got past the byte check and that the member
  is stored — not which candidate, and never a password or a plaintext byte. Documented and
  deliberate; the alternative is accepting a wrong password one time in 256.
- The real-world case behind the cross-platform name policies arrived as a ZIP: a macOS
  archive containing a `stuff_etc.` folder, a trailing dot Win32 silently trims. Because
  the offending segment is a directory, rejecting it takes every member beneath it. See
  [ADR 0013](../decisions/0013-cross-platform-name-safety-policies.md) and O3.

## 5. Sharp edges

*Where it lives*: **format** — inherent, no implementation fixes it · **library** — a
library archivey decodes with, fixable only upstream or by replacing it · **archivey** —
ours.

| What you see | Where it lives | More |
| --- | --- | --- |
| A ZIP from a pipe opens only with `streaming=True`, and its members arrive without the fields only the directory holds: no mode, host or comment, and a symlink is a `FILE` until the pass reaches the directory | **format** | The index is at the end (§1). The pass updates the members in place from the directory and says so through `member_state_final`; extraction applies the same fields to disk at the end of the pass (§2.2.2) |
| From a pipe, a few crafted shapes fail a member, or leave files on disk, that a file read handles otherwise | **format** / **archivey** | The data goes by before the directory that would correct it arrives; matching the file read would mean buffering every member (ADR 0010). The list is in §2.2.2 |
| A name whose UTF-8 flag is set over bytes that are not UTF-8 decodes as an unflagged name, with `MEMBER_NAME_ENCODING_INFERRED`, and `DiagnosticPolicy.STRICT` refuses it | **archivey** | The bytes refute the flag, so it cannot be honoured; reading the name as unflagged keeps every other member and the name itself, where refusing cost at least the rest of the listing. The diagnostic's `declared_encoding` is `"utf-8"` and its `inferred_encoding` the codec used (§2.2) |
| A `.z01`…`.zip` split set is refused with "rejoin first", while a `.zip.001`…`.00N` set beside it opens | **archivey** | Not an inconsistency: the first is a true spanned set addressed by (disk, offset), which the format defines perfectly well and a native reader could follow, and which a linear join reconstructs only for whichever members happen to sit on the last disk (§3); the second is `7z -v` byte slices that rejoin into an ordinary ZIP (§3). The native ZIP reader exposes each entry's disk number but keeps spanned sets refused; reading them stays an idea ([`IDEAS.md`](../IDEAS.md)). Filename rules catch `.zNN`; EOCD disk fields catch Info-ZIP's final `.zip` part (`0xFFFF` is the ZIP64 sentinel, not a disk number). [`docs/formats.md`](../../docs/formats.md#zip) tells users |
| A single `.zip.001` handed over without its siblings is refused rather than read as a ZIP | **archivey** | Joining needs parts `1..N` beside it. The part opens with `PK\x03\x04`, so it looks like a ZIP to a detector, but the central directory is in the *last* part, so no end record is found and not even a listing is available. "Rejoin first" names the actual problem. A numbering gap is `TruncatedError` instead |
| An archive cut before its end record lists its members without mode, host or comment, then raises | **format** | Those fields live only in the directory. With no end record in the tail, the reader walks the local headers, lists what they hold as a pipe read does before its directory, then raises the walk's error (`TruncatedError` for a cut). A damaged *directory* lists the entries before the damage and the local headers' past it, then raises (§2.2) |
| A legacy name that is not valid UTF-8 renders garbled and no setting fixes it | **format** | Unless the writer added a Unicode Path field (§2.2), which Info-ZIP `zip` does and many writers do not, every candidate codepage decodes every byte, so there is no oracle, and a filename is far too short for a statistical detector. The garble is honest and `raw_name` round-trips; a wrong guess is neither. Opt-in detection is post-1.0 ([`IDEAS.md`](../IDEAS.md)) |
| A wrong ZipCrypto password can be accepted, and a damaged ZipCrypto member reads as a password error | **format** | One-byte verifier. With several candidates, confirmation narrows it; nothing eliminates it. Data that then fails its CRC or decompressor raises `EncryptionError` naming both causes, because a damaged member read with the right password fails the same way |
| After a forward seek in a STORED ZipCrypto member, no CRC checks it, and a wrong password that passed the check byte goes unnoticed | **format** / **archivey** | The CRC is the only content check ZipCrypto has, and it covers the plaintext in order. A seek back keeps it, and so does a compressed member's forward seek, whose skipped bytes the verifier reads and hashes (ADR 0014, since 2026-10-10). A STORED member's forward seek jumps, so a read past it gives the CRC up and returns whatever the key produced. `ENCRYPTED_MEMBER_UNVERIFIED` (`reason="seek"`) says so on close when only the check byte vouched for the password (§2.3). WinZip AES keeps its HMAC across seeks |
| A ZIP over 4 GiB written without ZIP64 (macOS Archive Utility) reads, with an `ARCHIVE_OFFSET_WRAPPED` per corrected offset (advisory: macOS writes and reads them by default, so `STRICT` reads them too) | **format** | The writer stores offsets modulo 2³². The walk adds 2³² to an offset below the furthest end of the members before it and keeps it only when a local header with the member's name is there (§3) |
| A large deflated member of a macOS ZIP can list with `size=None` until it is read | **format** | Archive Utility stores sizes modulo 2³² and records the real size nowhere. Deflate's ratio settles it for members under about 4.16 MB compressed; for larger ones, `extra["size_mod_2_32"]` holds the stored one and a read to the end fills `size` in after the length and CRC-32 checks (§3). Members without the writer's fingerprint keep their stored sizes |
| A ZIP with a digital signature record (`PK\x05\x05`, APPNOTE 4.3.13) between its directory and its end record is refused with `CorruptionError` ("Bad magic number") | **archivey** | The end record's directory size and offset do not count the record, so its bytes shift where the directory is taken to start, the way prepended data does. stdlib `zipfile` refuses it the same way, and a read from a pipe matches the file read. Few writers produce the record; reading past it is not planned |
| A prefixed ZIP behind bytes that look like neither an executable nor a script is not detected, though it opens with `format=ZIP` | **archivey** | The tail probe is designed and unshipped (§2.1) |
| An LZMA member 7-Zip wrote with `-mm=LZMA:lc=8` (any `lc + lp` over 4) raises `UnsupportedFeatureError`, though 7-Zip reads it | **library** | The member is valid: the format allows `lc + lp` up to 12 and 7-Zip decodes it. liblzma, which stdlib `lzma` wraps, decodes `lc + lp` up to 4 only (`LZMA_LCLP_MAX`) and fails the rest with `LZMAError: Internal error`. Left unsupported by maintainer decision, 2026-10-08 (§6). 7z members behave the same ([`7z.md`](7z.md) §5) |
| A member whose local header stores another name, or a real CRC-32 where the directory stores 0, reads with `MEMBER_LOCAL_NAME_DIFFERS` (which `STRICT` refuses) or `MEMBER_LOCAL_CRC_USED` (which `STRICT` reads); one whose two headers both understate its size is refused with `CorruptionError` | **archivey** | Found in real archives (a backup-drive scan, [§3.3-3.5](../investigations/2026-10-backup-scan.md)). `unzip` reads all three. The name case is another code page, or `\` against `/`; the directory's name is used, so the local one decides nothing. The CRC case is every Adobe AIR package's `META-INF/AIR/hash`; the data is checked against the local value. The size case is refused because the declared size is the bound that stops decoding (DR-9a). A file read sees the name only when it opens the member's data, and only a file or link reports it: extraction reads no directory's data, so neither read reports a directory's. A file read reads the local header of a zero-CRC file or link (not AE-2, whose HMAC checks it) when it lists it, one positioned read per such member; a directory's zero CRC keeps no digest from either read (`DIGEST_UNVERIFIABLE`). From a pipe, the pass reports both at the directory; under `STRICT` only the name case raises there, with every member already written (§2.2.2), and the CRC case is recorded |

## 6. Decisions

| Choice | Why | Rejected |
| --- | --- | --- |
| archivey's own parser for the end record, central directory and local headers ([ADR 0020](../decisions/0020-native-zip-reader.md), superseding [0006](../decisions/0006-stdlib-zipfile.md)) | Zero-dependency still. Stdlib parsed the whole directory at open before `ListingLimits` saw a member, lost the archive over one damaged entry, varied by Python version and was reached through private names and exception text; the parser lists lazily, lists up to damage, and behaves the same everywhere | Stdlib `zipfile` (ADR 0006); `python-libarchive-c`, faster and broader at the cost of a native dependency |
| Report bytes after the end record as `ARCHIVE_TRAILING_DATA` | DR-3: the TAR, 7z, RAR and ISO rule. 7-Zip warns on any tail; zero padding stays silent here as there | Ignoring the tail, as Info-ZIP `unzip` and stdlib do |
| Refuse at open an end record declaring more entries than `max_members` | Every walk keeps an offset per entry for the overlap guard, so the count bounds memory in every mode (DR-15b); the directory must be 46 bytes per declared entry, so a wrong count field alone does not refuse a small archive | Charging only on `members()`, which left a streaming pass unbounded |
| Read members whatever "version needed" says | DR-4: the method and flags say what a member needs; 7-Zip reads such members | Stdlib's refusal of the whole archive above 6.3 |
| archivey's codec layer for member data | Stdlib decodes four methods; the codec layer decodes seven, and unifies CRC verification and error translation with the other backends | Staying on `ZipExtFile`, which left ZIP advertising codecs it could not decode |
| Read a non-seekable source forward over the local headers under `streaming=True`, and refuse it under `streaming=False`, rather than spool it | Silent buffering hides an unbounded memory or disk cost the caller did not ask for; the local headers make one forward pass possible (the maintainer, 2026-10-10) | Transparent `SpooledTemporaryFile`; still possible later as an explicit opt-in ([ADR 0010](../decisions/0010-no-silent-buffer-nonseekable.md)) |
| Apply the fields only the directory holds at the end of a forward pass (2026-10-02) | Matches the file read on disk without buffering anything (ADR 0010) | Treating every member as a regular file, as libarchive's streaming reader does (a symlink becomes a file: DR-1); spooling the archive to read the directory first |
| From a pipe, keep the files a directory-wide refusal finds after they were written (davi, 2026-10-14; the file read is to stop refusing these instead) | The pass learns of a mislabelled UTF-8 name only after every member is written; it raises the file read's error and leaves the files | Taking them back, as a member failure at the end of the pass does |
| Seeking and concurrent member streams off by default, on ZIP too | A rewind always re-decodes from the start of that member (or its nearest seek point), which is why both stay opt-in even here — not merely because TAR/7z can be worse. Concurrent opens also share one archive handle — `zip_reader.py` keeps a single `fp` behind its own re-entrant lock (a source whose read closes the archive re-enters it on the same thread), for a path source exactly as for a caller-supplied stream, so it is serialized seeks (multiplexing) either way rather than a second handle. ZIP's advantage is only the absence of cross-member decode dependency, plus a source that is always seekable for random access, because a pipe is read only by the forward pass, whose streams never seek. The strict default is reversible before 1.0; the permissive one is not | Enabling them where member access is `DIRECT` ([ADR 0003](../decisions/0003-member-streams-opt-in.md)) |
| Sniff unflagged names for UTF-8 validity; do not guess legacy codepages | Validation is near-conclusive; guessing has no oracle and a plausible wrong name is worse than a visible garble | An off-the-shelf charset detector, which can override a *valid* UTF-8 string with a legacy guess |
| Join `7z -v` byte slices; reject Info-ZIP spanned sets | The first are slices of one finished archive and rejoin exactly — archivey already rejoins the identical split for `.7z.NNN`, so refusing here answered the same input two ways. A linear join of the second lists correctly and then reads only the members that happen to sit on the last disk (§3) | Refusing both (the shape of the rule was the filename, not the structure); concatenating spanned segments and hoping |
| Extras named by capability, not by format | The codecs are shared, so `[7z]` told a ZIP reader to install support for a different format — the name lied, not the message | Per-format extras |
| Refuse PKWARE Strong Encryption at member open, not implement it | Without these checks, a member marked only by extra `0x0017` (no bit 6) was decrypted as ZipCrypto: a wrong-password or corruption error, or with the right password bytes handed back as plaintext. Checking bit 6 too gives a bit-6 member a message archivey owns (it used to reach stdlib's own `NotImplementedError`) and refuses a symlink's target before its data is read. Implementing it is out: patent-encumbered and rare outside PKZIP. No real archive of this kind is in the corpora, so the tests use re-flagged ZipCrypto members | Leaving the misleading error; implementing SES |
| Create-only writing, if and when writing lands | ZIP append is legal in the format and turns an interrupted write into a corrupt archive | In-place append (`history/ARCHITECTURE.md` §5.4) |
| Short ZipCrypto header is `TruncatedError` on both password paths; a declared size too small for the encryption header is `CorruptionError` for ZipCrypto and WinZip AES alike | A short read is physical EOF; callers matching `TruncatedError` vs `CorruptionError` would otherwise see a dispatch-dependent split. A declared size under 12 bytes (ZipCrypto) or under salt + verifier + HMAC (WinZip AES) is an impossible header in a file that did not end, so it is checked before the read and is damage, the same for both schemes; for WinZip AES it is also checked before the `cryptography` availability gate, so the type does not depend on the install (DR-5) | Mapping the confirm path's `BadZipFile` through the generic ZIP translator (`CorruptionError`); leaving the split |
| ZipCrypto decrypts in archivey, ahead of the codec layer, not in stdlib's `ZipExtFile` | One pipeline for every member: ZipCrypto members gain the three methods stdlib cannot decode, the fused verifier and the codec layer's seek, and share WinZip AES's password ladder. Stdlib's decrypter is pure Python too, so nothing is slower | Keeping `ZipExtFile` for ZipCrypto, which left it the one exception to the shared stream layer |
| Several WinZip AES candidates are confirmed like ZipCrypto ones | The first candidate to pass the 2-byte `pw_verify` used to win outright, so a colliding wrong password ahead of the right one failed the read on the HMAC. A confirm walk costs one extra read of a small or uncompressed member, only when there is more than one candidate | Accepting on `pw_verify` (2⁻¹⁶ per wrong candidate) |
| WinZip AES seeks by restarting CTR at the target's block, and keeps the HMAC | CTR decrypts from any offset (counter `1 + offset // 16`), so a seek costs one block. The HMAC covers the ciphertext, not the plaintext, so it survives seeks: the HMAC takes in ciphertext while reads continue its hashed prefix, and the read that returns the last byte reads the rest from the source, without decrypting, before it returns. That keeps the only check an AE-2 member has, and the candidate confirmation, under rapidgzip's own out-of-order reads too, so accelerators stay on | Refusing to seek, which broke the `seekable_members=True` guarantee; forfeiting the HMAC on a seek as a CRC is forfeited, which let an accelerator's seeks silently drop it; turning accelerators off over the AES stage |
| A WinZip AES member every candidate fails on the HMAC raises `CorruptionError`, as one password does | A wrong password passes `pw_verify` once in 65 536, so damage is far likelier, and one error type for both password paths means a symlink with a damaged target lists the same way under either (S28-K4, davi 2026-09-26) | ZipCrypto's ambiguous `EncryptionError`, which fits an 8-bit check but made the same damaged AES member raise two different types depending on how many passwords the caller passed |
| From a pipe, the first of several inconclusive password candidates wins (davi, 2026-10-14) | The walk to the end that tells them apart from a file needs the data twice. It only matters past the kept 1 MiB, for a STORED member or one under a codec that accepts random input, where a wrong candidate passed the cheap check (2⁻⁸ ZipCrypto, 2⁻¹⁶ AES). A wrong winner fails at its CRC or HMAC like one unconfirmed password. A choice that may change once spool limits are designed | Spooling the whole ciphertext, walking every survivor to its end and serving the winner from the spool; refusing several passwords from a pipe |
| A damaged symlink target leaves the link listed without a target | One member's damage says nothing about the others, and listing raised for the whole archive. The link is reported (`target_data_damaged`) and raises when opened or extracted; a strict policy still refuses the archive | Raising from `members()`, which took every other member with it |
| WinZip AES HMAC from the completing read, not `close()` | ADR 0014: `close()` is teardown. STORED members used to drain the MAC on close and raise `CorruptionError` there; compressed members already skipped it because the decompressor borrows the decrypt stream (S1-F1). Removing the drain makes both match CRC members | Wiring compressed members to authenticate on close too (the S1-F1 "fix" that would add a behaviour the ADR already ruled out) |
| Owner ids read as unzip 6.00 reads the four fields | DR-6, the format's official tool. Measured with `unzip -X` 6.00 (the Ubuntu 24.04 package) as root on crafted local headers, 36 field orders, "none" meaning the file stayed owned by root: each `0x7875` or `0x7855` decides afresh (the last one wins, a damaged or unknown-version one leaves none, a `0x7855` after a `0x7875` is ignored, a PKWARE field before either is discarded); `0x000d` and `0x5855` count only with neither present, the last one with ids winning and a short one changing nothing; new Unix ids of 1 or 3 bytes leave the file root-owned. The madler/unzip source keeps PKWARE ids under a later short `0x7855` or `0x7875`; the released 6.00 does not, and 6.00 is what users run. The difference is one mask in `ef_scan_for_izux`: 6.00 (Debian's 6.0-28 source) has `flags &= 0x0ff`, which clears `EB_UX2_VALID` (`1 << 8`), where the mirror has `flags &= ~0x0ff`. For 3-byte ids 6.00 does not decline: `read_ux3_value` has no case for 3, returns true without writing, and unzip chowns to an uninitialised `z_uidgid`, which happened to read 0. Archivey reports `id_size` instead; do not "fix" it into a chown to 0. A damaged deciding field is reported as `MEMBER_HEADER_RECORD_SKIPPED`, the Unicode Path rule below; an unknown version is silent, as for that field | A fixed ranking where the highest field present decides (wrong for half the orders); falling through to the next readable field; dropping damage silently |
| Times from the PKWARE (`0x000d`) and Info-ZIP old (`0x5855`) Unix fields read as unzip 6.00 reads them: an Extended Timestamp with data anywhere wins, a `0x7855` or `0x7875` before the field hides it, the last field long enough for both times wins, and they sit above NTFS `0x000A` (2026-10-15) | DR-6, the format's official tool, and macOS `ditto` and Archive Utility write only `0x5855`, so without it a macOS archive lists naive local DOS times (Maccy.app.zip 8 hours off). Measured with `unzip -qo` 6.00 (Debian 6.0-28, the Ubuntu 24.04 package) on crafted local headers in eleven field orders, by the extracted file's `st_mtime` and `st_atime`; `tests/test_zip_extra_fields.py::test_unix_field_times_match_unzip` repeats it where `unzip` is installed. unzip on Unix does not read NTFS times at all, so a blob with both gives the Unix field's | A fixed ranking by tag, which gets the `0x7855`/`0x7875`-before case wrong; NTFS above the Unix fields, which no Unix tool does |
| Keep the central extra fields no value was taken from as `extra["zip.extra_fields"]`, `{tag: data}`, first copy per tag (davi, 2026-10-15, in the thread on common ZIP extras) | Archivey reads every field the surveyed archives carry, so most members keep nothing, and a caller with a vendor field still gets it. A field archivey knows but took nothing from stays in, so a caller sees what was ignored. A tag leaves the dict when a later version reads it; davi accepted that, to be weighed case by case and noted in the changelog. Reopen if callers need the local header's fields | The raw central blob for every member (+4-5% listing memory for fields archivey already reads); parsed `(tag, data)` pairs for every field (+12-23%); not exposing extras |
| A forward pass yields no owner; the directory's is applied at the end of the pass, as the mode is | Extraction applies an owner as it writes each member, and a `chown` cannot be taken back when the directory gives another owner or none (a `0x7855` the local header alone carries, or any field a writer leaves out of the directory). Waiting for the directory holds the file read's owner on disk under `TRUSTED` as root by construction, not by trusting which fields a writer copies | Yielding the local header's ids and replacing them at the end of the pass; reading only the tags a directory usually also carries (`0x7875`, `0x000d`), which rests on what writers do |
| A malformed Unicode Path field drops the field, not the archive, on every Python version | The RAR5 extra-record ruling ([`rar.md`](rar.md) §6): an optional record that cannot be parsed costs that record, reported as `MEMBER_HEADER_RECORD_SKIPPED`, and the name it would have given is absent, so the header's bytes name the member. Stdlib 3.12+ refused the archive over two of the shapes and warned outside the diagnostics system over the third, while 3.11 and 7-Zip open it; the native parser reads the field itself, so every version agrees | Keeping stdlib's refusal on 3.12+; silencing the warning with `warnings.catch_warnings`, which changes process-wide state and is not thread-safe; dropping the field silently |
| An unknown size past 4 GiB in a macOS ZIP is `None` with `extra["size_mod_2_32"]`, filled in after a verified full read (davi, 2026-10-15, project thread: option A; the extra's name is his proposal) | Nothing reported is wrong, and the unknowns are limited to deflated members over about 4.16 MB compressed in Mac archives; the fingerprint keeps other writers' sizes as stored. Smaller members, most of a typical archive, keep exact sizes | Decoding such members while listing (exact sizes, but listing an archive of videos reads all of it); listing the stored remainder with a diagnostic (a wrong number) |
| Size corrections report a new code, `MEMBER_SIZE_WRAPPED` with `SizeWrapContext` (davi, 2026-10-16, project thread decision card) | Sizes and offsets are different facts with different fields; one code per fact keeps each context simple to match on | Reusing `ARCHIVE_OFFSET_WRAPPED` with optional size fields on its context; no diagnostic, leaving `size=None` and the extra key as the only trace |
| No lower deflate bound: only the upper one, 1032 to 1, narrows the size (davi, 2026-10-16, project thread: a lower bound only settles members truly over 4 GiB, and those can as well be over 8 GiB) | Empty stored blocks pad a valid stream without bound, so any lower bound picks a size 4 GiB too large for a padded stream with a matching CRC-32 (seen in review). The upper bound is deflate's own and a read checks it | A lower bound of seven eighths of the compressed size, which holds for real encoders and kept more sizes exact, but reported a wrong size for valid data |
| `ARCHIVE_OFFSET_WRAPPED` is advisory, outside `ARCHIVE_INTEGRITY_CODES` (davi, 2026-10-15, project thread) | macOS Archive Utility writes a ZIP past 4 GiB this way by default and reads it back, so `STRICT` would refuse an ordinary Mac archive; a correction is kept only when a local header with the member's name is there, and the CRC-32 is still checked, the `MEMBER_SPECIAL_FILE_HAS_DATA` pattern (DR-25). Reopen if a writer other than macOS is found producing wrapped offsets that a correction misreads | The integrity set, under DR-3 (the stored offsets misdescribe where good data sits) |
| Read a member whose central and local headers disagree on the name, or whose central CRC is 0 while the local CRC is right; keep refusing one whose two headers both understate the size (davi, 2026-10-10) | The first two are found in real archives (another code page or `\` against `/` in the local name; every Adobe AIR package), `unzip` reads both, and archivey takes every field from the central directory, so the local name decides nothing; the data is still checked against the local CRC. The third is refused because the declared size is the bound that stops decoding (DR-9a). The name case reports `MEMBER_LOCAL_NAME_DIFFERS`, which strict refuses because another tool shows another name; the CRC case reports `MEMBER_LOCAL_CRC_USED`, which strict reads because the data is still checked (davi, 2026-10-15). Both carry `LocalHeaderContext`. Reopen if a real writer produces the third shape at scale. Shipped with stage 5 of the native ZIP reader | Refusing all three, which loses archives `unzip` reads; reading the third, which drops the size bound |
| Two stored names that decode to the same name stay ordinary duplicates (davi, 2026-10-10) | Once each name decodes on its own, `c3 a9` (valid UTF-8) and `82` (cp437) can both become `é.txt`. The later member supersedes the earlier like any duplicate (`is_current=False`, `SUPERSEDED` on extraction) and the two `raw_name`s differ, so nothing is lost and no new public name is needed. Reopen if a real archive shows a collision a user needs both copies of | A dedicated diagnostic or a renaming rule |
| AppleDouble sidecars (`._name`, `__MACOSX/`) stay ordinary members with no marking key (davi, 2026-10-16, project thread) | Marked by name alone, a key adds nothing over the name filter in `docs/formats.md` and also marks a real file named `._x` | `extra["macos.resource_of"]` set from the name (#839, closed). Reopen with a mark that checks the AppleDouble magic in the data (`IDEAS.md`) |
| Leave LZMA1 `lc + lp` over 4 unsupported | 7-Zip writes it only when asked (`lc=8`). Decoding it needs an LZMA1 decoder other than liblzma | `pylzma` 0.6.1, which binds 7-Zip's own LZMA SDK and does decode these (an `lc=8` ZIP member read back byte-identical, one-shot and through `decompressobj`), but ships only an sdist (a C compiler for every install) under the LGPL, the reasons it was rejected for BCJ2 too; a pure-Python LZMA1 decoder (very slow); handing such members to the `7z` CLI (archivey has no 7z CLI backend). `backports.lzma` and libarchive both use liblzma |

## 7. Open questions

Gaps in what *we* know, not in what the format says — each would change something here if
answered, and none can be settled by reading more code. Distinct from §5, which is
behaviour a caller already sees.

None open. The heading stays so the section numbers after it, which briefs cite, do not
move.

## 8. Verify

```bash
./scripts/test.sh tests/test_zip.py tests/test_zip_aes.py \
    tests/test_zip_native_codecs.py tests/test_zip_multipassword.py \
    tests/test_volumes.py tests/test_volume_missing_part.py \
    tests/test_zip_extra_fields.py
```

| Claim | Pinned by |
| --- | --- |
| Cost receipt, central-directory lookup without I/O | `tests/test_zip.py::test_cost_receipt`, `::test_central_directory_lookup_no_io` |
| Non-seekable refused at open under `streaming=False`, naming `streaming=True`; read under `streaming=True` | `::test_non_seekable_zip_fails_fast`, `::test_non_seekable_zip_fails_fast_via_detection`, `::test_non_seekable_zip_opens_via_detection_when_streaming` |
| From a pipe: the walk and where each member's data ends | `tests/test_zip_local_walk.py`, `tests/test_zip_walk_codec_end.py` |
| From a pipe: members updated from the directory, `is_current` by directory order, unlisted local entries dropped, failures at the end of the pass, limits | `tests/test_zip_pipe.py`, `tests/test_zip_pipe_directory_checks.py` |
| From a pipe: encrypted members, one password, several, a provider | `tests/test_zip_pipe_encrypted.py` |
| From a pipe: extraction settled at the end of the pass, selection made again, failures at the end | `tests/test_zip_pipe_extract.py`, `tests/test_zip_pipe_reparse.py` |
| Every fixture read from a pipe and from a file gives the same members, data and files, except the recorded differences | `tests/test_zip_pipe_parity.py` |
| Spanned set and unjoinable segment refused | `::test_split_segment_name_rejected`, `::test_infozip_spanned_set_still_refused`, `::test_sevenzip_split_segment_without_siblings_rejected`, `::test_eocd_nonzero_disk_fields_rejected`, `::test_zip64_end_record_nonzero_disk_fields_rejected`, `::test_volume_shaped_name_honours_explicit_non_zip_format`, `tests/test_volumes.py::test_lone_numbered_volume_names_missing_parts` |
| A numbered part that is not on disk is `FileNotFoundError`, not an incomplete set | `tests/test_volume_missing_part.py` |
| Split checks do not fire on single-volume archives | `::test_eocd_zip64_disk_sentinel_still_opens`, `::test_plain_prefixed_and_empty_zip_still_open` |
| `7z -v` set joined, read across a part boundary, opened from any part | `::test_sevenzip_split_zip_set_is_joined_and_read`, `::test_sevenzip_split_zip_set_opens_from_a_middle_part`, `::test_sevenzip_split_zip_set_with_missing_part_is_truncated` |
| Numbered-part discovery, ordering and gap rejection, `.zNN` left alone | `tests/test_volumes.py::test_discover_zip_volume_siblings_natural_order`, `::test_discover_orders_parts_when_base_contains_partN`, `::test_discover_infozip_zNN_is_not_a_numbered_volume_set`, `::test_join_volumes_rejects_numbering_gaps`, `::test_discover_prefers_the_opened_names_padding_over_a_stray`, `::test_discover_prefers_the_opened_names_spelling_over_a_case_variant`, `::test_numbered_set_with_a_stray_of_another_width_opens`, `::test_join_volumes_refuses_one_part_named_in_two_paddings` |
| Joiner caches a few Path handles, cursor on sequential read | `tests/test_volumes.py::test_concatenated_file_backwards_seek_across_volume_boundaries`, `::test_concatenated_file_alternating_seek_reuses_cached_handles`, `::test_concatenated_file_handle_cache_evicts_past_capacity`, `::test_concatenated_file_cache_miss_reopens_beyond_capacity`, `::test_concatenated_file_sequential_read_does_not_search_offsets`, `::test_concatenated_file_mixed_path_and_stream`, `::test_concatenated_file_path_open_error_surfaces_on_read`, `::test_concatenated_file_missing_path_fails_at_construction` |
| Timestamp precedence; an out-of-range NTFS time is an issue; the extended timestamp, a signed 32-bit field, is always a valid date (pre-1970 included) | `::test_extended_timestamp_beats_ntfs`, `::test_ntfs_timestamps_used_when_no_extended_timestamp`, `::test_extended_timestamp_pre_epoch`, `::test_extended_timestamp_pre_epoch_does_not_depend_on_gmtime`, `tests/test_timestamps.py::test_filetime_out_of_range_is_an_issue`, `::test_unix32_to_datetime_covers_every_32_bit_value` |
| A bad timestamp's diagnostic names the member field, the creation slot by host | `tests/test_zip.py::test_bad_ntfs_times_name_the_member_field` |
| Times from the PKWARE and Info-ZIP old Unix fields in each field order, from a file and a pipe, and against `unzip` where it is installed | `tests/test_zip_extra_fields.py::test_unix_field_times`, `::test_unix_field_times_match_unzip`, `::test_macos_archive_times_are_utc` |
| `extra["zip.extra_fields"]`: unknown and unused known fields kept, first copy per tag, a field whose times were all replaced kept, absent when every field was read, set by a pipe pass at the directory, and charged per field against `max_metadata_bytes` | `tests/test_zip_extra_fields.py::test_unparsed_extra_fields`, `::test_unicode_path_field_is_read_when_it_names_the_member`, `::test_stale_unicode_path_field_is_unparsed`, `::test_unparsed_fields_count_against_the_listing_limit` |
| Encoding sniff, fallback, override, escalation | `::test_unflagged_utf8_name_is_sniffed` and the four tests after it |
| Owner ids from each of the four fields, their ranking, the central directory's short copies, and a damaged deciding field dropped with `MEMBER_HEADER_RECORD_SKIPPED` (strict refuses); a pass yields no owner and leaves the same owner on disk as a file read | `tests/test_zip.py::test_unix_owner_extra_fields` and the tests after it, through `::test_trusted_as_root_owner_matches_from_a_pipe`; `::test_trusted_applies_the_directory_owner_from_a_pipe` checks the applied owner without root |
| Unicode Path field names the member over the sniff and `encoding=`; a stale, unknown-version or local-only field is ignored | `tests/test_audit2_zip.py::test_unicode_path_extra_field_names_the_member` and the tests after it |
| A short, empty, cut-short or non-UTF-8 Unicode Path field is dropped with `MEMBER_HEADER_RECORD_SKIPPED` on every Python version, with no stdlib warning; strict refuses; a flagged name's field is not consulted | `tests/test_audit2_zip.py::test_malformed_unicode_path_field_is_dropped_with_a_diagnostic` and the three tests after it |
| Backslash by origin | `::test_backslash_converted_for_dos_windows_entry`, `::test_backslash_kept_literal_for_unix_entry` |
| Symlink target from member data; encrypted target withheld | `::test_symlink_member`, `::test_encrypted_symlink_listing_without_password` |
| Windows reparse points: a file symlink's buffer decoded, a directory one's absent data, a stored junction buffer setting the flag | `tests/test_windows_reparse.py` |
| Duplicate names read independently | `::test_duplicate_member_names_read_independently` |
| Overlapping-entry bomb; entries sharing a local header read once | `::test_overlapping_entries_bomb_translated_to_corruption`, `::test_entries_sharing_one_local_header_read_only_once` |
| A damaged directory lists up to the damage, then the local headers past it; version needed above 6.3 reads; trailing bytes reported; one walk for listing then opening, one index pass when `member_count` comes first, a seekable streaming pass reads the directory then forward only; a listed member keeps no extra field | `tests/test_zip_native_reader.py` |
| `max_members` refused at open from the declared count, charged by the walk when the count lies | `tests/test_listing_limits.py::test_zip_applies_max_members_at_open`, `::test_zip_walk_charges_max_members_when_the_declared_count_lies` |
| The parser against stdlib on every fixture, ZIP64, stubs, disk fields | `tests/test_zip_parser.py` |
| A member's input ends with its stream: LZMA without an end marker and PPMd with junk or a declared size short of their data, DEFLATE and bzip2 with junk or a second stream, accelerators off and on, are `CorruptionError`; one zero byte after marker-less LZMA reads; a Zstd member's second frame is refused | `tests/test_surplus_input.py`, `tests/test_audit2_zip.py::test_bytes_after_a_deflate_members_stream_are_corrupt`, `::test_bzip2_member_with_a_second_stream_is_corrupt` |
| AE-1/AE-2, wrong password, tampered ciphertext | `tests/test_zip_aes.py` |
| Bytes after the codec's end inside a member (zero, zeros, junk) raise for DEFLATE, Deflate64, BZip2, PPMd (in process and in a child) and Zstd, also under the accelerators (for DEFLATE on a seek to the end too); `7z t` fails on the same members | `tests/test_zip_native_codecs.py::test_zip_member_with_input_after_its_stream_is_corrupt`, `::test_zip_zstd_member_with_input_after_its_frame_is_corrupt`, `::test_zip_input_after_the_stream_is_corrupt_under_the_accelerator`, `::test_zip_ppmd_input_after_the_end_mark_is_corrupt_in_a_child_process`, `tests/test_accelerator_takeover.py::test_a_seek_to_the_end_refuses_input_after_a_container_streams_end` |
| A Zstd member with a second frame or a skippable frame after its first frame is `CorruptionError`; the shared framed decoder refuses a second stream | `tests/test_zip_native_codecs.py::test_zip_zstd_member_with_a_second_frame_is_corrupt`, `::test_zip_zstd_member_with_a_skippable_frame_is_corrupt`, `::test_framed_stream_refuses_a_second_stream_after_the_end` |
| Bytes after the stream inside a ZipCrypto member (the `EncryptionError` of an unconfirmed password, caused by the `CorruptionError`) or a WinZip AES member raise | `tests/test_zip_native_codecs.py::test_zip_zipcrypto_member_with_input_after_its_stream_is_corrupt`, `::test_zip_winzip_aes_member_with_input_after_its_stream_is_corrupt` |
| A PPMd member with no end mark is `CorruptionError`, in process and in a child; an end mark already decoded at the size is checked; a worker the end-mark probe parks is quiesced on close | `tests/test_zip_native_codecs.py::test_zip_ppmd_member_without_an_end_mark_is_corrupt`, `tests/test_ppmd_raw_streams.py::test_ppmd8_end_mark_decoded_before_the_size_check_reads_unused_data`, `::test_ppmd8_end_probe_that_parks_the_worker_quiesces_it_on_close` |
| A second bzip2 stream in a member is `DataAfterEndError`, whatever the size and CRC cover, where `zipfile` ends the member at the first | `tests/test_audit2_zip.py::test_bzip2_member_ends_at_its_first_stream`, `::test_bzip2_member_with_a_second_stream_after_its_end_is_corrupt` |
| Tampered HMAC raises on a full read (STORED and DEFLATE); partial read then `close()` is quiet | `tests/test_zip_aes.py::test_aes_tampered_hmac_raises_corruption`, `::test_aes_tampered_hmac_partial_read_then_close_is_quiet` |
| AES decrypt stream `close()` still releases the source after a partial read; a source `OSError` still marks the wrapper closed | `::test_aes_decrypt_stream_close_releases_source`, `::test_aes_decrypt_stream_close_marks_wrapper_closed_when_source_raises` |
| Our AE-1 fixtures cross-checked against an independent implementation | `tests/test_zip_aes.py::test_handbuilt_ae1_is_accepted_by_7z` |
| A third-party AE-1 archive reads, with the CRC exposed and verified | `::test_external_ae1_archive_from_pyzipper` |
| ZipCrypto candidate confirmation, STORED CRC pass; ZipCrypto over Zstd; ZipCrypto seeks; stage output equals stdlib's | `tests/test_zip_multipassword.py` |
| The STORED CRC pass reads through the same validated local header as the open, so it never runs over a member the open refuses | `tests/test_zip_multipassword.py::test_stored_confirm_reads_through_the_validated_local_header` |
| From a pipe, short members' data and kept ciphertext are not charged to `max_metadata_bytes`; the longest is dropped first past it, and a dropped link raises; the archive comment is weighed at the end record | `tests/test_zip_pipe.py::test_small_files_data_is_not_charged_to_max_metadata_bytes`, `::test_kept_ciphertext_is_not_charged_to_max_metadata_bytes`, `::test_the_kept_data_drops_the_longest_first`, `::test_the_archive_comment_counts_toward_max_metadata_bytes` |
| A local name that differs from the directory's, and a central CRC-32 of 0 under a right local one, read from a file and a pipe, strict refusing the name case only; a wrong local CRC-32 and 0 in both still fail; both headers understating the size stays refused | `tests/test_audit_backup_scan.py::test_local_name_in_another_codepage_still_reads` and the tests after it, `tests/test_zip_pipe.py::test_a_name_that_differs_between_the_two_headers_takes_the_directory_name`, `tests/test_zip_pipe_extract.py::test_a_name_the_two_headers_disagree_on_is_written_by_the_directory_name`, `tests/test_zip_multipassword.py::test_a_zero_central_crc_takes_the_check_byte_from_the_local_crc` |
| WinZip AES seeks at every block phase and backward; the HMAC survives seeks, including over bytes a seek skipped and on a `read(n)` ending exactly at the end; a partial read after a seek has no verdict | `tests/test_zip_aes.py::test_aes_decrypt_stream_seeks_at_every_block_phase`, `::test_aes_decrypt_stream_seeks_keep_the_hmac`, `::test_aes_decrypt_stream_seek_to_current_position_keeps_the_hmac`, `::test_aes_decrypt_stream_catches_tampered_ciphertext_a_seek_skipped`, `::test_aes_decrypt_stream_partial_read_after_seek_has_no_verdict`, `::test_aes_member_seeks`, `tests/test_member_stream_contract.py` (`zip-aes` rows) |
| A tampered AE-2 DEFLATE member still raises under rapidgzip's own seeks (`AUTO` and `ON`, one password and two) | `tests/test_zip_aes.py::test_aes_hmac_survives_a_seekable_accelerator` |
| A lone ZipCrypto password that passed the check byte: a partial read is reported unverified, a read to EOF and extraction are not; a forward seek on a compressed member raises the password-or-damage error, and a seek on a STORED one is reported as `seek` | `tests/test_encrypted_member_unverified.py`, `tests/test_zip_multipassword.py::test_single_colliding_password_fails_a_forward_seek`, `::test_single_colliding_password_stored_seek_reports_unverified` |
| A seek then a full read of a weakly accepted AES member is not reported unverified; a seek then a partial read is, as `partial_read` | `tests/test_encrypted_member_unverified.py::test_winzip_aes_seek_then_full_read_is_not_reported`, `::test_winzip_aes_seek_then_partial_read_is_reported_as_a_partial_read` |
| A method-99 member with bit 0 clear lists as encrypted and asks for a password | `tests/test_zip_aes.py::test_method_99_without_encryption_flag_lists_as_encrypted` |
| WinZip AES candidates confirmed, not taken on `pw_verify` | `tests/test_zip_aes.py::test_aes_candidate_passing_pw_verify_does_not_shadow_the_right_one` |
| A failing HMAC is `CorruptionError` with a candidate list too, including when only colliding wrong candidates reach it | `tests/test_zip_aes.py::test_aes_tampered_hmac_with_candidates_raises_corruption`, `::test_aes_only_colliding_candidates_report_damage` |
| A damaged symlink target (CRC, HMAC, declared size) lists the link targetless with `target_data_damaged`; open and extraction raise; strict refuses | `tests/test_damaged_link_target.py`, `tests/test_link_target_cap.py::test_a_zip_target_longer_than_its_declared_size_is_corruption` |
| PKWARE Strong Encryption refused at open, a symlink of it listed with the target unset; unrelated extra records still read; an encrypted central directory recognized where the directory starts, a damaged one (and a record at a stale declared offset) still `CorruptionError` | `tests/test_zip.py::test_strong_encryption_member_is_unsupported`, `::test_strong_encryption_symlink_lists_with_target_unset`, `::test_zipcrypto_member_with_unrelated_extra_still_reads`, `::test_encrypted_central_directory_is_unsupported`, `::test_encrypted_zip64_central_directory_is_unsupported`, `::test_record_inside_a_damaged_zip64_directory_is_not_read_as_encryption`, `::test_damaged_central_directory_stays_corruption`, `::test_record_at_the_stale_declared_offset_is_not_read_as_encryption` |
| Truncated ZipCrypto header is `TruncatedError` on both password dispatch paths; a declared size too small for the ZipCrypto or WinZip AES header is `CorruptionError` naming the member; codec-path and member-read `IndexError` stay raw; CONCURRENT stamp releases the handle lock | `tests/test_zip.py::test_truncated_zipcrypto_header_is_typed_error` (`single` / `multi`), `::test_encryption_header_larger_than_declared_size_is_corruption`, `tests/test_zip_aes.py::test_aes_header_too_short_is_corruption_without_crypto`, `::test_unencrypted_codec_indexerror_is_not_truncated`, `::test_unencrypted_member_read_indexerror_is_not_truncated`, `::test_truncated_zipcrypto_stamp_releases_handle_lock` |
| Cross-format member equivalence, per-method decode, AE-2 CRC absence | `tests/test_corpus_sweep.py` (13 ZIP corpus entries) |

**Building fixtures.** Stdlib `zipfile` cannot write encryption, so encrypted fixtures shell
out to `7z` (`-mem=AES256` for WinZip AES, ZipCrypto by default) and skip when it is absent —
one of the ~109 tests that vanish quietly on an unprovisioned container. Nothing on the
image writes **AE-1**, so those fixtures are hand-built by `tests/zip_aes_fixture.py`; what
keeps that builder honest is `7z` accepting its output while rejecting the same bytes with
a corrupted CRC, plus one committed third-party sample
([`tests/fixtures/external/README.md`](../../tests/fixtures/external/README.md)). `-mm=Deflate64`,
`-mm=PPMd`, `-mm=BZip2` and `-mm=LZMA` produce the extended methods. The backslash fixtures
are committed rather than generated because `zipfile` rewrites `ZipInfo.filename` on Windows;
the reader uses `orig_filename` for the same reason.

The joined-set fixtures shell out to `7z a -tzip -mx0 -v40k` — a real split from the tool
whose behaviour is being matched, and cheap because `-mx0` stores. The Info-ZIP shapes are
**synthesised instead**: `zip` is not installed on CI, macOS or Windows, so a `zip -s`
fixture would skip on every machine that matters and prove nothing about the refuse path.

## 9. References

- [APPNOTE.TXT](https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT): §4.3.6 overall format · §4.3.9 data descriptor · §4.3.16 EOCD · §4.4.4
  general-purpose flags (bits 0, 3, 6, 11 and 13) · §4.4.6 MS-DOS date/time · §4.4.15
  external file attributes · §4.5.5 extended timestamp · §4.5.12 strong encryption header
  (`0x0017`) · §4.3.11 archive extra data record · §6 traditional encryption · §7 strong
  encryption · §8 splitting and spanning · Appendix D CP437
- Specs: [`format-zip`](../../openspec/specs/format-zip/spec.md) ·
  [`compressed-streams`](../../openspec/specs/compressed-streams/spec.md) ·
  [`format-detection`](../../openspec/specs/format-detection/spec.md)
- Code: `internal/backends/zip_reader.py` · `internal/backends/zip_forward_reader.py` and
  `internal/backends/zip_local_walk.py` (the forward pass) · `internal/backends/zip_detect.py` ·
  `internal/backends/zipcrypto.py` · `internal/backends/zip_aes.py` · `internal/volumes.py` (numbered-part
  discovery and joining, shared with 7z)
- Investigations: [`archive-format-detection-algorithm.md`](../investigations/archive-format-detection-algorithm.md)
  (tail-tier design, corpus counts) ·
  [`rar-corpus-sweep-diagnosis.md`](../investigations/rar-corpus-sweep-diagnosis.md)
  (per-format symlink digest and size comparison)
- User-facing: [`docs/formats.md`](../../docs/formats.md#zip) ·
  [`docs/gotchas.md`](../../docs/gotchas.md)
