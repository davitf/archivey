# Design — native ZIP reader

## Context

`zip_reader.py` (2 615 lines) uses `zipfile.ZipFile` for one job: find the end record and
turn the central directory into `ZipInfo` objects. Member data already bypasses
`ZipExtFile`: the reader parses the local header itself (`_local_data_region`), slices
the payload with a `SharedView`, runs its own ZipCrypto and WinZip AES stages and decodes
through the shared codec layer. So the native reader replaces the directory parse and the
glue around it, and leaves the member data path as it is.

The ZIP fix PRs from the October code sweep (trailing bytes after a codec's end, the
Unicode Path field, comment decoding) change the same file. Coding starts after they
merge, and their tests become acceptance tests for the native reader.

## Layout

### `internal/backends/zip_parser.py` (new)

Structure only, like `sevenzip_parser.py`: no `ArchiveMember`, no passwords, no codecs,
no diagnostics collector. It raises `CorruptionError`, `TruncatedError` and
`UnsupportedFeatureError` and returns plain data.

```python
@dataclass(frozen=True, slots=True)
class EndRecord:
    eocd_offset: int          # absolute position of PK\x05\x06
    zip64: bool               # a ZIP64 end record supplied the fields below
    entries_declared: int
    cd_size: int
    cd_offset: int            # as stored
    base: int                 # added to every stored offset (stub before the archive)
    comment: bytes            # cut at end of file
    comment_declared: int     # the stored length, for the "comment cut short" finding
    trailing: int             # bytes after the record and its declared comment (seekable only)

ReadAt = Callable[[int, int], bytes]   # read_at(offset, n); the caller owns handle and lock

def find_end_record(read_at: ReadAt, file_size: int) -> EndRecord: ...

@dataclass(frozen=True, slots=True)
class CentralEntry:
    index: int                # position in the directory
    version_made_by: int      # create system in the high byte
    version_needed: int
    flags: int
    method: int
    dos_time: int             # raw, for the ZipCrypto check byte under bit 3
    dos_date: int
    crc: int
    compressed_size: int      # after ZIP64 extra resolution
    file_size: int
    header_offset: int        # absolute: stored offset + EndRecord.base
    disk_start: int
    internal_attr: int
    external_attr: int
    name: bytes               # stored bytes, never decoded here
    extra: bytes
    comment: bytes

class CentralDirectoryWalk:
    def __init__(self, read_at: ReadAt, end: EndRecord) -> None: ...
    def __iter__(self) -> Iterator[CentralEntry]: ...
    findings: list[EndRecordFinding]   # complete once iteration ends

@dataclass(frozen=True, slots=True)
class LocalHeader:
    flags: int
    crc: int                  # 0 under bit 3
    compressed_size: int
    file_size: int
    name: bytes
    extra: bytes
    data_start: int           # absolute

def read_local_header(read_at: ReadAt, header_offset: int) -> LocalHeader: ...
```

`find_end_record` does what four helpers in `zip_reader.py` and stdlib's `_EndRecData`
do today, in one place:

- The search is stdlib's, so prefixed and commented archives resolve to the same record:
  a comment-less record ending at end of file first, then the last `PK\x05\x06` in the
  final 65 557 bytes.
- The ZIP64 locator 20 bytes before it, then the ZIP64 record it points to.
- A disk field naming another disk (`0xFFFF` is the ZIP64 sentinel), or a ZIP64 locator
  with more than one disk, is `UnsupportedFeatureError` with `ZIP_MULTI_VOLUME_MSG`. No
  exception text is matched.
- An archive extra data record (`PK\x06\x08`) where the directory should start is the
  Strong Encryption refusal, checked before the walk rather than guessed after a failure.
  ZIP64 archives are covered too, which today they are not.
- `base` is stdlib's `concat`: `eocd_offset - cd_size - cd_offset` (minus the ZIP64
  records when present). A self-extractor stub whose writer did not adjust the offsets
  keeps reading. With `start_offset` the reader already slices the source, so `base` is
  computed inside the slice as today.

`CentralDirectoryWalk` reads the directory forward in 1 MiB pieces, one entry at a
time. The piece size trades memory (one piece plus one entry) against the number of
reads, each a seek and a read on the shared handle: a directory of 1 MiB or less is one
read, and a 46 MB one is 46. It is an implementation constant (DR-9). Every read goes
through `read_at`, which the reader implements under its lock with the handle position
saved and restored, so the parser knows nothing of locking. Each entry resolves its
ZIP64 extra field (`0x0001`) in the order APPNOTE gives, reading only the fields whose
32-bit value is `0xFFFFFFFF`. It stops at `cd_size` bytes, as stdlib does, and cuts a
name, extra or comment that runs past that point, which is one of the findings.

The walk takes no limit and no charge callback. An entry's variable fields are three
16-bit lengths, so the most one entry can make the parser hold is 46 + 3 × 65 535 bytes,
plus the piece it is reading: the format bounds it, not the archive. The reader charges
each member it builds against `max_members` and `max_metadata_bytes` before it asks the
walk for the next entry, so the listing limits apply entry by entry. TAR differs here:
a header declares the size of the record after it, so the TAR parser charges that size
before reading it.

`read_local_header` is today's `_local_data_region` without the name decode: it compares
the local name **bytes** with the central name bytes, so a name that is not valid UTF-8
can no longer raise `UnicodeDecodeError` there.

### `zip_reader.py`

- `member._raw` holds the `CentralEntry`. Every `info.X` read maps to an entry field:
  `orig_filename` to `name` (no cp437 round trip), `_raw_time` to `dos_time`,
  `date_time` to a decode of `dos_date`/`dos_time`, `is_dir()` to "name ends in `/`".
- The handle is `self._source` (or the seek-counting and slicing wrappers over it), for a
  path source too. stdlib opened its own file for a path; the reader now opens nothing
  itself, and the path-or-handle branch in `__init__` goes.
- A reader-owned `threading.Lock` replaces `ZipFile._lock`. `SharedView`, the local
  header read, `_payload_is_complete` and `_close_archive` take it, as they take the stdlib
  lock today.
- `_iter_members` drives a `CentralDirectoryWalk` and yields each member as its entry is
  parsed. `ListingLimits` therefore see members as they are read, not after the whole
  directory has been built. The end-record findings are emitted after the last member, as
  today.
- `_get_archive_info`: `member_count` is the number of entries a complete walk read. A
  walk that fails, or stops at `max_members`, reports `None`, following DR-1 (absence
  over a wrong value). The count comes from the listing walk when that has run to the
  end, and otherwise from the index pass below; the directory is never walked a third
  time. The cost receipt stays `INDEXED`, `DIRECT`, `SEEKABLE` for a seekable source;
  §"What the caller sees in a forward pass" has the forward-pass values.
- At open, an archive whose end record declares more entries than `max_members` raises
  `ResourceLimitError`, when the directory is large enough to hold that many (`cd_size`
  at least 46 bytes per declared entry). That is the cheap check at open (DR-15b). It
  applies under `streaming=True` too, for a seekable source, which reads the directory
  first (question C). A declared count the directory cannot hold is a wrong
  field, not a breach: it stays the count-mismatch finding, and the walk charges what is
  really there.

### The overlap guard

stdlib refuses a member whose payload runs into the next local header ("Overlapped
entries", the zip-bomb shape). The bound for a member is the smallest header offset above
its own, or the directory start. That needs every offset, and the walk is lazy.

The listing walk records each entry's header offset as it goes (8 bytes per entry, in an
`array('Q')`), so after a listing that ran to the end the offsets and the count are
already known. Only when a member is opened, or `member_count` is asked for, before the
listing has finished does the reader run an **index pass**: one more pass over the fixed
46-byte fields that builds no members and keeps only the offsets and the count. Both
collections charge `max_members`: the index pass stops past it. Once it has stopped,
**every** member open raises the listing's `ResourceLimitError`, not only one above the
stop, because the bound of a member below it could come from an incomplete set of
offsets. Listing alone never pays for the array beyond the offsets of the members it
listed.

`max_members` bounds the array in every mode, so ZIP charges it even under
`streaming=True`, where listing limits are otherwise off. That moves ZIP into the list
of formats that apply `max_members` while parsing (7z, RAR and ISO, `archive-reading`;
a stage 3 spec edit). A seekable source under `streaming=True` walks the directory
first, so the walk charges it there. A forward pass has no offset array (it reads data
in order), but it keeps every member it yielded until the end of the pass to update it
in place, so it charges `max_members` as members are yielded.

### Trailing bytes

ZIP reports bytes after its end record as TAR reports bytes after its trailer
(`format-tar`, the 1 MiB scan), so the one ruling gives one behaviour:

- The scan starts at the end of the declared comment and reads at most 1 MiB, in
  bounded chunks, in both modes. The first non-zero byte is reported as
  `ARCHIVE_TRAILING_DATA` with `observed_bytes` its offset past the comment; zeros are
  silent; past the bound the scan stops and reports nothing. The bound is TAR's
  `_MAX_TRAILING_SCAN` (measured there at about 5 ms), shared, not a second constant.
- A seekable source skips the scan when `EndRecord.trailing` is 0, which is free from
  the file size. A forward pass has no size, so it reads; a pipe held open after the
  ZIP ends blocks there until more bytes or end of file, as a TAR pipe does today
  (`docs/gotchas.md` says so for TAR and gains ZIP).
- `expected_marker` is the existing `"zeros_to_eof"`, with `format="zip"`. Its meaning
  is the same (only zeros may follow the end marker, to end of file), so no new public
  value is added; the `diagnostics` spec row and the `ArchiveEofContext` docstring
  change from naming the TAR trailer to naming each format's end marker. 7z, RAR and
  ISO can share it under the same ruling; their thread decides, and this design does
  not need them to.

## Streaming: a forward walk over local headers

A ZIP can be read start to end with no seek, from a pipe, a socket or a remote object
read once: each member's local header sits right before its data, and the central
directory follows the last member. The parser supports this walk, and the reader uses it
for `streaming=True` on a non-seekable source, which ZIP refuses today. ZIP then sets
`SUPPORTS_STREAMING_NON_SEEKABLE`.

### Parser support

The fixed-header parsing is shared by both walks; only where the bytes come from
differs. `parse_central_header(buf, pos)` and `parse_local_header(buf, pos)` work on
bytes. `CentralDirectoryWalk` feeds them from `read_at`. The forward walk takes the
source as a `BinaryIO` that it only reads, never seeks, and counts the bytes it has
consumed, which is the offset every local entry is matched by. That is the shape
`TarWalker` uses for a forward-only stream; there is no new reader type.

```python
class LocalEntry:                 # what one local header says, plus where it was
    offset: int                   # bytes consumed before its signature
    header: LocalHeader           # incl. ZIP64 extra applied to the sizes
    sizes_known: bool             # False under bit 3: the data descriptor has them

class LocalHeaderWalk:
    def __init__(self, stream: BinaryIO) -> None: ...
    def __iter__(self) -> Iterator[LocalEntry]: ...   # the caller consumes each body
    def finish_member(self, consumed: int, crc: int) -> DataDescriptor | None: ...
    def central_directory(self) -> Iterator[CentralEntry]: ...  # after the last member
    end: EndRecord | None           # set once the end record has been read
```

The walk stops at the first `PK\x01\x02`, `PK\x06\x06` or `PK\x05\x06` after a member;
the directory is then read forward from the same stream with the same entry parser.

### Where a member's data ends

| Member | How the walk finds its end |
| --- | --- |
| Sizes in the local header (bit 3 clear) | `compressed_size` bytes |
| Bit 3, a codec with an end marker (DEFLATE, Deflate64, bzip2, Zstandard, LZMA with the EOS bit, PPMd with its end mark) | The codec's own end, then the data descriptor: signature optional, 8-byte sizes when the local header has a ZIP64 extra field, else 4-byte. Its CRC and sizes are checked against what was read |
| Bit 3, STORED (stdlib `zipfile` writes this to a pipe: measured, every member) | Scan forward for `PK\x07\x08` followed by a CRC and a compressed size that match the bytes since the data start, and then by a local header, central header or end record signature. libarchive reads it the same way. A descriptor without its signature cannot be found this way: `UnsupportedFeatureError` |
| Bit 3, STORED under ZipCrypto or WinZip AES | The same scan, on the ciphertext. The descriptor's CRC covers the plaintext, so the scan matches on the compressed size and the signature after it only; the CRC (ZipCrypto, AE-1) or the HMAC (AES) then checks the plaintext as for any member |
| Bit 3, LZMA without the EOS bit, PPMd without an end mark | No way to find the end: `UnsupportedFeatureError` in this mode, naming the reason. A seekable source reads it |

The STORED scan can be fooled on purpose: a member that carries, inside its own data, a
descriptor for a prefix of itself followed by a local header signature ends early, and
its CRC matches. The central entry is the check that catches it: a size or CRC that came
from a data descriptor and differs from the central entry is a failure of that member at
the end of the pass (§"Failures found at the end of the pass"). This is not question A,
which is about two headers that state the same field differently; here the local header
stated no size at all.

### What only the central directory says

The external attributes (Unix mode, symlink and special-file bits, the DOS reparse
bit), "version made by" (the host, which decides `created` against `ctime`, and how a
backslash reads) and the member comment are in the central directory only. In a forward
read they arrive after every member. Measured: libarchive's streaming reader (`bsdtar
-xf - < x.zip`) never reads them, and writes an Info-ZIP symlink as a 5-byte regular
file with mode 0664.

archivey reads them and applies them at the end of the pass, so that streaming and
seekable extraction end up the same on disk (the 2026-10-02 ruling):

- `stream_members()` yields each member with what its local header gives (name, flags,
  method, sizes, times from the local extra fields). `mode`, `create_system` and
  `comment` are `None` until the directory has been read, not guessed. The type cannot
  be `None`: it is `FILE` or `DIRECTORY` from the name until then. Then the members
  already yielded are updated in place (`ArchiveMember` is mutable, ADR 0007), before
  the pass ends. Each such member has `member_state_final` false until then (question
  D), so a caller sees the gap on the member before reading anything (DR-8).
- Extraction writes each member as it arrives, then at the end of the pass applies
  modes, turns a member the directory types as a symlink into a link (its target is the
  data just written, at most the link-target cap), and removes what it wrote for a
  member the directory types `OTHER`. It only touches what it created (DR-18). This is
  the same end-of-pass step that already resolves links in a streaming pass.
- A member whose type the directory changes goes through the caller's `filter` and the
  extraction policy's checks **again**, on the updated member (name, type, link
  target), before the link is made, exactly as `extract_all` already calls the filter
  again for a link target it reads after the filter ran (`safe-extraction`, the
  `read_link_targets` paragraph). If either refuses it, the regular file written for it
  is removed and the refusal is reported as for any refused member. A symlink that
  escapes the destination is therefore refused from a pipe as from a file. Links are
  made only after the last member has been written, so no member is ever written
  through one.
- The directory is authoritative. Each local entry is matched to its directory entry by
  offset. A local entry no directory entry points to (an appended update, a planted
  member) is removed from disk and reported: data outside any member is a warning
  (DR-3), and the seekable read never sees it. A name, CRC or size that differs
  between the two follows the answer to question A, as in seekable mode. A directory
  entry with no local entry in the stream is a failure of that member at the end of the
  pass (DR-2; next section).

### Other shapes the walk meets

- A stub before the first local header (self-extractors): when the detector reports
  `payload_offset`, the walk starts there. The detector finds it only within its scan
  budget (2 MiB, or 256 KiB under `FAST`), and a caller can name the format and skip
  detection. When the stream does not start with a local header or an end record and no
  offset was given, the walk scans forward for a local header that the detector's ZIP
  hit validator (`validate_zip_local_header`) accepts, within the same 2 MiB window
  (`SFX_MAX`), and starts there. Nothing found in the window is `CorruptionError`
  naming the reason. In the seekable read a missed stub costs nothing, because `base`
  recovers the offsets; the forward walk has no end record to recover them from until
  the end.
- Bytes between the last member and the directory (an APK signing block, for one) are
  read past to the directory signature. The end record, read last, confirms where the
  directory started; the seekable read skips the same bytes without reading them.
- An empty archive: the stream starts with the end record.
- Order: members come in file order. A seekable read lists them in directory order.
  Writers put both in the same order; when they differ, member ids follow the
  directory once it has been read, and so does `is_current`: of two members with the
  same name, the current one is the later in the directory, in both modes. A streaming
  extraction writes copies in file order, so when the directory order of two same-name
  members is the reverse of their file order, the file on disk holds the copy the
  directory supersedes. That is a failure of the current member at the end of the pass
  (§"Failures found at the end of the pass"). Writers do not produce this; a crafted or
  hand-edited archive does. `stream_members()` raises nothing for it: the members are
  updated in place, `is_current` included, and `member_state_final` said so.

### Failures found at the end of the pass

Three things are known only once the directory has been read: a size or CRC from a data
descriptor that differs from the central entry, two same-name members whose directory
order reverses their file order, and a directory entry with no local entry. Each is a
member-scoped failure of the member it names, handled as `safe-extraction` handles any
other (the `OnError` requirement):

- In `extract_all`, the end of the pass settles every member the directory changes
  (its `is_current`, type or sizes), and every path such a member was written to:
  - **The member's own result** becomes the one the seekable extraction of the same
    archive gives it, revised in place as the `OVERWRITTEN` rows revise a result. Where
    that result needs bytes the stream has already passed (the member is now current
    but its data was not written, or its data failed the descriptor check), it becomes
    `FAILED` with `CorruptionError` instead. A directory entry with no local entry never
    had a result, so one is added, `FAILED`. Results stay in member-processing order.
  - **The path.** After the revisions, an entry this run wrote stays only if the member
    whose result names it is `EXTRACTED`. Any other entry archivey wrote there is
    removed (DR-18), and the path is left empty: the bytes the seekable extraction
    would put there are behind in the stream.
  - For the reversed pair (file order `M1`, `M2`; the directory makes `M1` current):
    during the pass `M1` was `SUPERSEDED` and `M2` `EXTRACTED` at the path. At the end
    `M2` becomes `SUPERSEDED` (non-current, never written in seekable mode), `M1`
    becomes `FAILED`, and `M2`'s entry is removed. For a descriptor mismatch the member
    is `FAILED` and its entry removed.
  - Under `OnError.CONTINUE` the report completes. Under `OnError.STOP` the first such
    failure raises `CorruptionError` naming the member, at the end of the pass, which is
    the earliest point it is known; the revisions and removals above are made first.
The first two make a streaming extraction fail a member that the seekable extraction of
the same archive writes. That is the only place the two access modes differ on disk.
Both shapes come only from crafted or hand-edited archives, which DR-5a allows to differ
a little; matching them would mean buffering every member until the directory arrives
(ADR 0010).

### What the caller sees in a forward pass

| Field | During the pass | After the end record |
| --- | --- | --- |
| `cost.listing_cost` | `REQUIRES_SCANNING`: members are found by walking local headers | same |
| `cost.access_cost` | `DIRECT`: each member's data is independent | same |
| `cost.stream_capability` | from the source (`FORWARD_ONLY`), as TAR does | same |
| `member_count` | `None` (DR-1): the count is in the end record | the number of directory entries |
| `comment` | `None` | the archive comment |
| `members_report_if_available()` | `None`, as `access-mode-and-cost` already states for a trailing index on a non-seekable source | the complete report, as for TAR after a completed pass |

`get_archive_info()` builds a new `ArchiveInfo` on each call, so a call after the pass
returns the count and the comment.

### Seekable sources under `streaming=True`

A seekable source in streaming mode reads the directory first: one read at the tail,
then forward reads only, and every member is complete when yielded (question C,
answered). The local-header walk is for non-seekable sources.

## What the parser removes

| Workaround today | Where | Replaced by |
| --- | --- | --- |
| `_zipfile_private("_EndRecData")` and six `_ECD_*` indices | `zip_reader.py:246-263` | `EndRecord` |
| `_find_classic_eocd`, a second end-record search | `:2257` | `find_end_record` |
| `_central_directory_overrun`, a second directory parse | `:2399` | walk findings |
| `_end_record_findings` re-reading the record stdlib read | `:2296` | walk findings |
| `_classic_eocd_declares_split` after stdlib listed a spanned part | `:2431` | disk fields in `find_end_record` |
| `_looks_like_multivolume`, matching `BadZipFile` text | `:2545` | the same |
| `_central_directory_looks_encrypted` after stdlib failed | `:2496` | checked before the walk |
| `ZipInfo._raw_time` for the ZipCrypto check byte | `:1161` | `CentralEntry.dos_time` |
| `ZipFile._lock` | `:1180` | reader-owned lock |
| `ZipInfo._end_offset` for the overlap guard | `:1266` | offset array |
| cp437 round trip of `orig_filename` to get the stored bytes | `:1013-1018` | `CentralEntry.name` |
| The `ZipFile` subclass overriding `_RealGetContents` (Unicode Path PR) | open PR | nothing: stdlib never sees the field |
| `UnicodeError` at open for a flagged name that is not UTF-8 | `:817` | per-name decode (stage 3) |
| `NotImplementedError` at open for version needed above 6.3 | `:826` | nothing at listing (below) |
| `BadZipFile` / `ValueError` / `NotImplementedError` arms in `_translate_exception` | `:863` | the parser raises typed errors |
| `_ZIP_MEMBER_READ_ERRORS` entries for stdlib's exceptions | `:326` | shrinks to the codec-side ones |

## Behaviour that changes

Each row lands in the stage PR that causes it, with the spec and handbook edits (DR-22).

| Input | Today | Native | Rule |
| --- | --- | --- | --- |
| A name flagged UTF-8 that is not valid UTF-8 | `CorruptionError` at open, nothing listed | The flag is wrong for that name: it decodes as an unflagged name does (UTF-8 is already ruled out, so `encoding=`, else the configured fallback) and `MEMBER_NAME_ENCODING_INFERRED` names the codec used, with `declared_encoding="utf-8"`. 7-Zip and `unzip` read the rest too | DR-2; the 2026-10-07 ruling that `encoding=` only replaces the fallback |
| A directory entry with a bad signature, or a directory the file cuts short | `CorruptionError` at open | The entries before it list; the walk then raises `CorruptionError` or `TruncatedError` at that entry | DR-2, as a cut RAR already does (list, then raise) |
| Version needed above 6.3 | `UnsupportedFeatureError` at open, nothing listed | Listed; a member raises `UnsupportedFeatureError` only when its method or a flag is one archivey cannot read | DR-4, DR-2. Measured on a STORED member marked 8.4: 7-Zip 23.01 ignores the field and reads it; `unzip` 6.0 skips that member and reads the rest. Both read the other members |
| Malformed Unicode Path field | Depends on the Python version until the Unicode Path PR merges | Same as that PR, on every version, without the subclass | DR-5 |
| `streaming=True` on a non-seekable source | `StreamNotSeekableError` at open | Read forward (§"Streaming") | The maintainer's request, 2026-10-10 |
| A ZIP64 archive whose directory is Strong-Encrypted | `CorruptionError` | `UnsupportedFeatureError` | DR-4 |
| Bytes after the end record and its comment | Nothing reported | `ARCHIVE_TRAILING_DATA` warning; strict refuses; zero padding stays silent. 7-Zip 23.01 warns on the same input, `unzip` says nothing. Examined as TAR's tail is, in both modes: at most 1 MiB past the comment's end, first non-zero byte reported, `expected_marker="zeros_to_eof"` (§"Trailing bytes") | The 2026-10-07 ruling (davi): report trailing data after ZIP, 7z, RAR and ISO, as TAR and the codecs already do. Stage 2 |
| A ZIP from a pipe whose data descriptor understates a STORED member, or whose directory lists two same-name members in the reverse of their file order | Not readable at all (non-seekable source refused) | That member fails at the end of the pass (`FAILED` under `OnError.CONTINUE`, a raise under `STOP`); the seekable read of the same archive writes it. The only on-disk difference between the two modes | DR-5a: only crafted archives have these shapes, and matching would mean buffering every member (ADR 0010). Stage 3 |
| A member flagged as a Windows reparse point whose data is not a reparse buffer, in a `stream_members()` pass | Yielded as a SYMLINK with no stream, retyped to FILE after the pass: its content is lost | Typed when the pass reaches it, by reading the bounded reparse header ahead and handing back a stream of the whole member, as 7z already does; `members()` already lists it as FILE. From a pipe the reparse bit arrives with the directory, so such a member is written as a file and stays one, and only a real reparse buffer becomes a link (§"What only the central directory says") | DR-5, DR-1. Stage 3 |
| Open of an archive with a huge declared directory | All `ZipInfo` built at open | Members built as listed; `ListingLimits` stop the walk | DR-9a, DR-15b |

Nothing else may change. The acceptance bar is the full suite as it stands once the
three ZIP fix PRs have merged, plus a differential test: for every ZIP under
`tests/fixtures/`, every archive `tests/create_adversarial.py` builds and the sample
corpus, the parser's entries match `zipfile.infolist()` wherever `zipfile` opens the
archive.

- **Compared, per entry:** the stored name bytes, header offset, both sizes, CRC, flags,
  method, create system, both version bytes, both attribute fields, extra, comment,
  `date_time`, the raw DOS time and whether the name ends in `/`. The disk number is
  compared unless stdlib holds the ZIP64 sentinel `0xFFFF`. Per archive: the directory
  start and the archive comment.
- **Not compared, and why:** the decoded name (the parser returns bytes; stdlib's
  `orig_filename` is re-encoded with the codec the flag names, which recovers the stored
  bytes exactly); `filename`, which 3.12+ replaces from the Unicode Path field; and
  `_end_offset`, which is not a stored field.
- **Where the parser parts from stdlib on purpose**, each its own test rather than the
  comparison: a cut or overrunning extra field (stdlib refuses the archive, the parser
  keeps it), a ZIP64 extra field that is absent although a value is deferred (both
  keep the 32-bit value), the decoy end record in a comment.
- **Interpreters:** the comparison runs on every Python in the CI matrix. stdlib's
  version differences (the Unicode Path field from 3.12) change only whether it opens
  an archive, never a compared field, so on each version the test compares what that
  version opens and a floor on the count of compared archives stops it comparing
  nothing.
- **Shown to fail** against three mutants: the header offset without `base`, the ZIP64
  sizes read compressed-first (caught only by a test with both sizes deferred and
  different, added for this), and the two attribute fields swapped.

## Stages

One PR each, in order; every PR goes through the review label.

1. **Parser.** `zip_parser.py` and its tests: unit tests on hand-built records, the
   differential test above, ZIP64 (sizes, offsets, entry count), stub prefixes. No reader
   change.
2. **Switch the reader.** `zip_reader.py` reads through the parser; every row of the
   removal table except the name decode. Damaged-directory and version-needed rows of the
   behaviour table, and trailing bytes reported (§"Trailing bytes"). ADR 0006 is
   superseded by a new ADR; format-zip spec, the `diagnostics` spec row and the
   `ArchiveEofContext` docstring for `"zeros_to_eof"`, `docs/gotchas.md` (trailing data
   and the open-pipe note), handbook §2.2, §5 and §6 updated.
3. **Streaming.** The forward local-header walk and the end-of-pass reconciliation,
   for non-seekable sources (seekable ones read the directory first, question C). `stream_members()` and
   `extract_all()` over a pipe, tested against the same archives read seekably: the
   files on disk must match. This stage changes a public declaration: ZIP's
   `SUPPORTS_STREAMING_NON_SEEKABLE` becomes true, so `required_source` for ZIP moves
   from `SEEKABLE` to `FORWARD_ONLY`. Specs and docs that move with it: format-zip,
   `backend-registry` (the `required_source` table), `access-mode-and-cost` (the
   trailing-index row), `safe-extraction` if the filter re-run needs a sentence there,
   `docs/access-and-cost.md` (the checklist tells users to buffer ZIP),
   `archive-data-model` (the new `member_state_final` field, every format),
   `archive-reading` (ZIP applies `max_members` at parse, in every mode) and the
   `ListingLimits` docstring in `config.py` that restates it, `docs/formats.md`, handbook
   §1, §2.2, §5, §6, and the TAR, 7z and RAR handbook pages for which of their fields
   `member_state_final` covers.
4. **Names.** The lying UTF-8 flag. Name collisions stay ordinary duplicates
   (question B, answered). Format-zip spec, handbook §2.2 and §5, `docs/formats.md`,
   and the DR-21 ruling in `design-rules.md`, which says the refusal lasts until this
   rewrite.
5. **Header disagreement.** Cases 1 and 2 read with a new diagnostic code (its name goes
   to the maintainer in the PR); case 3 stays refused (question A, answered).
   Format-zip spec, `docs/errors-and-diagnostics.md`, handbook §5, and
   the backup-scan investigation's §3.3-3.4 status.
6. **ZIPs over 4 GiB without ZIP64.** macOS Finder writes a classic ZIP past 4 GiB and
   stores every offset modulo 2³². The rule recorded in `IDEAS.md`: offsets must
   increase, so when one falls below the previous member's end, add 2³² until it does
   not; the end record's directory offset follows the same rule. It applies only when a
   local header with the entry's name sits at the corrected offset, the CRC check stays,
   and a diagnostic records the correction. A single member over 4 GiB (its sizes wrap
   too) stays out until a real archive from a current Mac settles it. The diagnostic may
   need a new code, which goes to the maintainer. Format-zip spec, handbook §3 and §5,
   `docs/formats.md`, and the `IDEAS.md` item, which this stage closes.
7. **Methods 1 (Shrink) and 6 (Implode).** Two small pure-Python decoders under
   `internal/streams/codecs/`, registered with the codec layer like the others (DR-20:
   no native code). Only DOS-era archives use them, so speed does not matter. They need
   `CompressionAlgorithm` members, which are public names and go to the maintainer.
   Format-zip spec, `docs/formats.md`, handbook §2.3.

Stages 6 and 7 are independent of 3 to 5 and can run beside them.

## Questions for the maintainer

A, B and C were answered by the maintainer (davi) on 2026-10-10 in the thread that
commissioned this change. The format-specific rulings (A, B) are recorded in
`dev-docs/formats/zip.md` §6 and C, which is not ZIP-specific, under DR-8 in
`dev-docs/design-rules.md`; the text below keeps the reasoning they were asked with.

**A. Central and local headers that disagree.** Answered (davi, 2026-10-10): read cases
1 and 2 with a new diagnostic (strict refuses), keep refusing case 3, as recommended
below. Archivey refuses a member in three cases that `unzip` reads, found by the
backup-drive scan (`dev-docs/investigations/2026-10-backup-scan.md` §3.3-3.5; each
pinned by an `xfail(strict)` test in `tests/test_audit_backup_scan.py`). ZIP has no
single official tool (design rules, Open gaps), so DR-6 does not settle it.

| Case | `unzip` | 7-Zip | Recommendation |
| --- | --- | --- | --- |
| 1. Name differs (another code page, or `\` against `/`) | warns, reads | reads | Read, with a diagnostic. archivey takes every field from the central directory, so the local name decides nothing; the spoofing risk is for tools that trust the local header |
| 2. Central CRC 0, local CRC right (every Adobe AIR package) | reads (checks the local CRC) | headers error | Read, verified against the local CRC, with a diagnostic. The data is still checked |
| 3. Both headers understate the size, CRC matches the full output | reads | fails | Keep refusing. The declared size is the bound that stops decoding (DR-9a), and one archive in the scan has it |

Reading cases 1 and 2 needs a new diagnostic code (a public name). Strict refuses both.

**B. Two stored names that decode to the same name.** Answered (davi, 2026-10-10): keep.
The per-name decode can turn `c3 a9` (valid UTF-8) and `82` (cp437) into the same
`é.txt`; the later member supersedes the earlier one like any duplicate
(`is_current=False`, `SUPERSEDED` on extraction, the two `raw_name`s differ). No new
public name.

**C. A seekable source under `streaming=True`.** Answered (davi, 2026-10-10): directory
first. One read at the tail, then forward reads only; every member is complete when
yielded (DR-8), and nothing needs fixing at the end. The local-header walk is for
non-seekable sources only.

**D. How a caller sees that a forward pass fills some fields only at the end.**
Answered (davi, 2026-10-10): a per-member field saying the member's state will not
change again, working name `member_state_final` (davi chose an explicit name over
`is_final`, which reads like `is_current` and `is_dir`; the stage 3 PR settles it).
The cost receipt was rejected: it describes what listing and reading cost, not whether
a member's fields are settled. The field is not ZIP-specific. It is false wherever
archivey can still update a member in place: a ZIP member yielded in a forward pass
until the directory has been read; any member of a forward-only pass in any format
(TAR included) until the pass ends, because a later member with the same name can still
make it `is_current=False`; and a link whose target is stored as member data until that
target has been read (`read_link_targets=False`, or a listing from the index alone). It
is true everywhere else, so a caller checks it on each member before reading anything.
Its documented meaning is "any field of this member may still change"; each format's
handbook page and `docs/formats.md` name which fields that is in practice (for a ZIP
forward pass: type, mode, host, comment, `is_current`; for a data-stored link: the
target).

## Out of scope

- Listing an archive whose directory is lost, by walking local headers forward. The
  walk above is the tool for it; it stays an idea for later (`IDEAS.md`).
- Info-ZIP spanned sets (`.z01`…`.zip`). The parser exposes `disk_start`, which is what a
  later reader would follow; they stay refused.
- Writing.

## Decisions

| Choice | Why | Rejected |
| --- | --- | --- |
| Parse lazily, member by member | `ListingLimits` see each member as it is read; open does no work proportional to the archive (DR-15b); a damaged entry costs only the entries after it (DR-2) | Parsing the whole directory at open, as stdlib does |
| Parser returns bytes; the reader decodes | Name decoding is policy (`encoding=`, fallback, diagnostics) that already lives in the reader and is shared in shape with RAR and TAR | Decoding in the parser, which is how stdlib ended up refusing whole archives |
| Keep stdlib's end-record search and `concat` rule | Prefixed archives and decoy signatures in comments resolve exactly as today, and the differential test can compare against stdlib | 7-Zip's search, which differs on crafted inputs and would change detection results |
| Stop the walk at `cd_size`, cutting an overrunning field | Same listing as today; the overrun stays a finding | Reading the field from past the directory, which changes names on crafted input |
| Apply central-only fields at the end of a forward pass | Matches seekable extraction on disk without buffering anything (ADR 0010) | Treating every member as a regular file, as libarchive does (a symlink becomes a file: DR-1); spooling the archive to find the directory first |
| Positioned reads through a `read_at(offset, n)` callable for the directory walk | The walk is lazy, so it runs between reads of member data on the same handle; a positioned read under the reader's lock leaves the handle where the member stream needs it, and the parser stays free of locking | A `LockedStream` over the handle, which serialises each call but shares one position, so every walk read would need a seek and a read as one locked step, which is `read_at` |
| The forward walk reads a `BinaryIO` and counts what it consumed | The same shape as `TarWalker` on a forward-only stream; no new reader type | A separate `ForwardReader` class |
| No charge callback into the ZIP parser | One entry is at most 46 + 3 × 65 535 bytes by the format, and the reader charges each member against the listing limits before asking for the next | Passing `Charge` as the TAR parser does, which it needs because a TAR header declares the size of the record after it |
| Keep `zipfile` in the tests | It writes most fixtures and is the oracle for the differential test | Dropping it, which loses the cheapest independent check of the parser |
