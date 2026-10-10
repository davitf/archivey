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

def find_end_record(handle: BinaryIO, *, file_size: int) -> EndRecord: ...

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
    def __init__(self, handle: BinaryIO, end: EndRecord, *, lock: Lock) -> None: ...
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

def read_local_header(handle: BinaryIO, entry: CentralEntry) -> LocalHeader: ...
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
at a time, under the reader's lock with the handle position saved and restored. Each entry
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
3. **Names.** The lying UTF-8 flag. Name collisions created by the per-name decode
   (open question B).
4. **Header disagreement.** Waits for open question A.
5. **ZIPs over 4 GiB without ZIP64.** macOS Finder writes a classic ZIP past 4 GiB and
   stores every offset modulo 2³². The rule recorded in `IDEAS.md`: offsets must
   increase, so when one falls below the previous member's end, add 2³² until it does
   not; the end record's directory offset follows the same rule. It applies only when a
   local header with the entry's name sits at the corrected offset, the CRC check stays,
   and a diagnostic records the correction. A single member over 4 GiB (its sizes wrap
   too) stays out until a real archive from a current Mac settles it. The diagnostic may
   need a new code, which goes to the maintainer.
6. **Methods 1 (Shrink) and 6 (Implode).** Two small pure-Python decoders under
   `internal/streams/codecs/`, registered with the codec layer like the others (DR-20:
   no native code). Only DOS-era archives use them, so speed does not matter. They need
   `CompressionAlgorithm` members, which are public names and go to the maintainer.

Stages 5 and 6 are independent of 3 and 4 and can run beside them.

## Open questions for the maintainer

**A. Central and local headers that disagree.** Archivey refuses a member in three cases
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

**B. Two stored names that decode to the same name.** The per-name decode can turn
`c3 a9` (valid UTF-8) and `82` (cp437) into the same `é.txt`. Today the later member
supersedes the earlier one like any duplicate: the earlier row stays listed with
`is_current=False`, extraction reports it `SUPERSEDED`, and the two `raw_name`s differ.
Options:

- **Keep (recommended).** It is already reported the way every duplicate is, and an
  archive needs two writers' conventions in it to hit the case.
- **A diagnostic when a collision comes from decoding** (new code, public name).
- **One codec per archive:** if any unflagged name is not valid UTF-8, decode every
  unflagged name with the legacy codec. That reverses the 2026-10-07 per-name ruling.

## Out of scope

- Reading from a non-seekable source, and listing an archive whose directory is lost, by
  walking local headers forward. Both need the parser this change adds; both are salvage
  or streaming work planned after 0.2.0 (`IDEAS.md`).
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
| Keep `zipfile` in the tests | It writes most fixtures and is the oracle for the differential test | Dropping it, which loses the cheapest independent check of the parser |
