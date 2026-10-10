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

`CentralDirectoryWalk` reads the directory forward in 64 KiB chunks (DR-10a), one entry
at a time. Every read goes through `read_at`, which the reader implements under its lock
with the handle position saved and restored, so the parser knows nothing of locking. Each entry
resolves its ZIP64 extra field (`0x0001`) in the order APPNOTE gives, reading only the
fields whose 32-bit value is `0xFFFFFFFF`. It stops at `cd_size` bytes, as stdlib does,
and cuts a name, extra or comment that runs past that point, which is one of the findings.

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
- `_get_archive_info`: `member_count` is the number of entries a complete walk read (one
  walk, cached as a count, with no members built). A walk that fails reports `None`,
  following DR-1 (absence over a wrong value).

### The overlap guard

stdlib refuses a member whose payload runs into the next local header ("Overlapped
entries", the zip-bomb shape). The bound for a member is the smallest header offset above
its own, or the directory start. That needs every offset, and the walk is lazy. The reader
builds a sorted array of header offsets the first time a member is opened, from one pass
over the fixed 46-byte fields (8 bytes per entry, and the pass is bounded by the directory
size, which the file size bounds). Listing alone never pays for it.

## Streaming: a forward walk over local headers

A ZIP can be read start to end with no seek, from a pipe, a socket or a remote object
read once: each member's local header sits right before its data, and the central
directory follows the last member. The parser supports this walk, and the reader uses it
for `streaming=True` on a non-seekable source, which ZIP refuses today. ZIP then sets
`SUPPORTS_STREAMING_NON_SEEKABLE`.

### Parser support

The fixed-header parsing is shared by both walks; only where the bytes come from
differs. `parse_central_header(buf, pos)` and `parse_local_header(buf, pos)` work on
bytes. `CentralDirectoryWalk` feeds them from `read_at`; the forward walk feeds them
from a `ForwardReader` over the source (read, never seek):

```python
class LocalEntry:                 # what one local header says, plus where it was
    offset: int                   # bytes consumed before its signature
    header: LocalHeader           # incl. ZIP64 extra applied to the sizes
    sizes_known: bool             # False under bit 3: the data descriptor has them

class LocalHeaderWalk:
    def __init__(self, reader: ForwardReader) -> None: ...
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
| Bit 3, STORED (stdlib `zipfile` writes this to a pipe: measured, every member) | Scan forward for `PK\x07\x08` followed by a CRC and a compressed size that match the bytes since the data start. libarchive reads it the same way. A descriptor without its signature cannot be found this way: `UnsupportedFeatureError` |
| Bit 3, STORED under ZipCrypto or WinZip AES | The same scan, on the ciphertext: the compressed size must match; the CRC (ZipCrypto, AE-1) or the HMAC (AES) checks the plaintext |
| Bit 3, LZMA without the EOS bit, PPMd without an end mark | No way to find the end: `UnsupportedFeatureError` in this mode, naming the reason. A seekable source reads it |

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
  method, sizes, times from the local extra fields). The central-only fields are unset
  and its type is `FILE` or `DIRECTORY` from the name, until the directory has been
  read. Then the members already yielded are updated in place (`ArchiveMember` is
  mutable, ADR 0007), before the pass ends.
- Extraction writes each member as it arrives, then at the end of the pass applies
  modes, turns a member the directory types as a symlink into a link (its target is the
  data just written, at most the link-target cap), and removes what it wrote for a
  member the directory types `OTHER`. It only touches what it created (DR-18). This is
  the same end-of-pass step that already resolves links in a streaming pass.
- The directory is authoritative. Each local entry is matched to its directory entry by
  offset. A local entry no directory entry points to (an appended update, a planted
  member) is removed from disk and reported: data outside any member is a warning
  (DR-3), and the seekable read never sees it. A name, CRC or size that differs
  between the two follows the answer to question A, as in seekable mode. A directory
  entry with no local entry in the stream raises at the end of the pass (DR-2).

### Other shapes the walk meets

- A stub before the first local header (self-extractors): the detector already reports
  `payload_offset`, and the walk starts there.
- Bytes between the last member and the directory (an APK signing block, for one) are
  read past to the directory signature. The end record, read last, confirms where the
  directory started; the seekable read skips the same bytes without reading them.
- An empty archive: the stream starts with the end record.
- Order: members come in file order. A seekable read lists them in directory order.
  Writers put both in the same order; when they differ, member ids follow the
  directory once it has been read.

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
| Open of an archive with a huge declared directory | All `ZipInfo` built at open | Members built as listed; `ListingLimits` stop the walk | DR-9a, DR-15b |

Nothing else may change. The acceptance bar is the full suite as it stands once the
three ZIP fix PRs have merged, plus a differential test: for every ZIP under
`tests/fixtures/` and every archive `tests/create_adversarial.py` builds, the parser's
entries match `zipfile.infolist()` field by field wherever `zipfile` opens the archive.

## Stages

One PR each, in order; every PR goes through the review label.

1. **Parser.** `zip_parser.py` and its tests: unit tests on hand-built records, the
   differential test above, ZIP64 (sizes, offsets, entry count), stub prefixes. No reader
   change.
2. **Switch the reader.** `zip_reader.py` reads through the parser; every row of the
   removal table except the name decode. Damaged-directory and version-needed rows of the
   behaviour table. ADR 0006 is superseded by a new ADR; format-zip spec, handbook §2.2,
   §5 and §6 updated.
3. **Streaming.** The forward local-header walk and the end-of-pass reconciliation,
   for non-seekable sources (seekable ones read the directory first, question C). `stream_members()` and
   `extract_all()` over a pipe, tested against the same archives read seekably: the
   files on disk must match.
4. **Names.** The lying UTF-8 flag. Name collisions stay ordinary duplicates
   (question B, answered).
5. **Header disagreement.** Cases 1 and 2 read with a new diagnostic code (its name goes
   to the maintainer in the PR); case 3 stays refused (question A, answered).
6. **ZIPs over 4 GiB without ZIP64.** macOS Finder writes a classic ZIP past 4 GiB and
   stores every offset modulo 2³². The rule recorded in `IDEAS.md`: offsets must
   increase, so when one falls below the previous member's end, add 2³² until it does
   not; the end record's directory offset follows the same rule. It applies only when a
   local header with the entry's name sits at the corrected offset, the CRC check stays,
   and a diagnostic records the correction. A single member over 4 GiB (its sizes wrap
   too) stays out until a real archive from a current Mac settles it. The diagnostic may
   need a new code, which goes to the maintainer.
7. **Methods 1 (Shrink) and 6 (Implode).** Two small pure-Python decoders under
   `internal/streams/codecs/`, registered with the codec layer like the others (DR-20:
   no native code). Only DOS-era archives use them, so speed does not matter. They need
   `CompressionAlgorithm` members, which are public names and go to the maintainer.

Stages 6 and 7 are independent of 3 to 5 and can run beside them.

## Open questions for the maintainer

**A. Central and local headers that disagree.** Answered 2026-10-10: read cases 1 and 2
with a new diagnostic (strict refuses), keep refusing case 3, as recommended below. Archivey refuses a member in three cases
that `unzip` reads, found by the backup-drive scan
(`dev-docs/investigations/2026-10-backup-scan.md` §3.3-3.5; each pinned by an
`xfail(strict)` test in `tests/test_audit_backup_scan.py`). ZIP has no single official
tool (design rules, Open gaps), so DR-6 does not settle it.

| Case | `unzip` | 7-Zip | Recommendation |
| --- | --- | --- | --- |
| 1. Name differs (another code page, or `\` against `/`) | warns, reads | reads | Read, with a diagnostic. archivey takes every field from the central directory, so the local name decides nothing; the spoofing risk is for tools that trust the local header |
| 2. Central CRC 0, local CRC right (every Adobe AIR package) | reads (checks the local CRC) | headers error | Read, verified against the local CRC, with a diagnostic. The data is still checked |
| 3. Both headers understate the size, CRC matches the full output | reads | fails | Keep refusing. The declared size is the bound that stops decoding (DR-9a), and one archive in the scan has it |

Reading cases 1 and 2 needs a new diagnostic code (a public name). Strict refuses both.

**B. Two stored names that decode to the same name.** Answered 2026-10-10: keep. The
per-name decode can turn `c3 a9` (valid UTF-8) and `82` (cp437) into the same `é.txt`;
the later member supersedes the earlier one like any duplicate (`is_current=False`,
`SUPERSEDED` on extraction, the two `raw_name`s differ). No new public name.

**C. A seekable source under `streaming=True`.** Answered 2026-10-10: directory first.
One read at the tail, then forward reads only; every member is complete when yielded
(DR-8), and nothing needs fixing at the end. The local-header walk is for non-seekable
sources only.

## Out of scope

- Listing an archive whose directory is lost, by walking local headers forward. The
  walk above is the tool for it; salvage is planned after 0.2.0 (`IDEAS.md`).
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
| Keep `zipfile` in the tests | It writes most fixtures and is the oracle for the differential test | Dropping it, which loses the cheapest independent check of the parser |
