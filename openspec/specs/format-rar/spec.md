# RAR Archive Support

## Purpose

Archivey parses RAR metadata natively (RAR 1.5 / 2.x through RAR5) with no
`rarfile` dependency. Listing uses the native parser only; reading compressed or
encrypted member data delegates to the system RARLAB `unrar` binary. RAR is
read-only, and `rarfile` is only a test oracle.

This native-metadata/system-decompressor split follows the `archivey-dev`
`rar-native-metadata-reader` exploration. A full native RAR decompressor is out
of scope because RAR compression is proprietary and `unrar` is the reference
implementation.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Read API, multi-source input, passwords, link following, bounded storage |
| `access-mode-and-cost` | Seek requirement, cost receipt, solid access semantics |
| `compressed-streams` | Pass-through stored reads and checksum verification |
| `packaging-and-extras` | RARLAB `unrar` binary and `[recommended]` availability |
| `testing-contract` | Native parser coverage and `rarfile` oracle checks |

## Requirements

### Requirement: Declare RAR format properties

The RAR backend SHALL expose these properties:

| Property | Value |
| --- | --- |
| Read dependency (metadata) | None; native RAR 1.5–RAR5 header parser |
| Read dependency (data) | RARLAB `unrar` or `rar` binary on `PATH` (`unrar` preferred) |
| Listing cost | O(1); headers parsed natively, no member-data decompression |
| Access cost | `SOLID` for solid archives; `DIRECT` otherwise |
| Supports write | No |
| Requires seek | Yes |

#### Scenario: format property matrix

| Case | Expected |
| --- | --- |
| Open non-header-encrypted RAR without `unrar`/`rar` | Listing and metadata still work through the native parser |
| Open from a non-seekable source | Open fails because RAR header parsing requires seek |
| Attempt to create/write RAR | `UnsupportedOperationError` |

### Requirement: Parse RAR headers natively (RAR 1.5 through RAR5)

The system SHALL parse RAR archive headers natively — including RAR 1.5 / 2.x
archives that advertise extract version ≤ 20, RAR3/RAR4, and RAR5 — to produce
the full member list and per-member metadata: names, packed/unpacked sizes,
timestamps, mode, flags, solid state, RAR5 redirect (`file_redir`) records,
encryption flags, and integrity hashes. Listing SHALL not import `rarfile` or
invoke `unrar`. Extract version ≤ 20 MUST NOT by itself cause rejection: those
archives share the same header block layout the parser already understands, and
member data remains RARLAB `unrar`'s responsibility. When a RAR5 MAIN locator
points at a stored, unencrypted `QO` SERVICE, the parser SHALL parse those
FILE copies, seek back to after MAIN, and walk. FILE headers whose offsets
appear in `QO` SHALL be emitted from the copies and skipped; FILE headers
`QO` omitted SHALL still be parsed. `CMT` after MAIN SHALL be consumed as a
normal SERVICE on that walk. Extract SHALL use the same table. Otherwise the
parser SHALL walk FILE headers. A `QO` that is missing, packed, split,
encrypted, CRC-invalid, or behind header encryption SHALL fall back to the
walk.

#### Scenario: native header matrix

| Case | Expected |
| --- | --- |
| Open RAR 1.5 / 2.x archive (extract version ≤ 20) | Members and metadata come from native headers; data via `unrar`; no `UnsupportedFeatureError` for extract version alone |
| Open RAR3/RAR4 archive whose members advertise extract version 20 | Listing and reads succeed (stored/small members often carry `unp_ver=20`) |
| Open RAR4 archive | Members and metadata come from native headers |
| Open RAR5 archive | Members, flags, hashes, and redirect metadata come from native headers |
| Open RAR5 with a stored unencrypted `QO` reachable from MAIN's locator | FILE headers in `QO` are emitted from the copies and skipped on the walk; omitted FILE headers and `CMT` after MAIN are parsed |
| Open RAR5 with no `QO`, locator offset 0, packed/encrypted/`QO` CRC failure, or header encryption | Member table is filled by the FILE-header walk |
| `unrar` missing during listing | Listing succeeds unless header decryption needs unavailable crypto/password |
| Extract version ≤ 20 alone | No `UnsupportedFeatureError` |

### Requirement: Accept a non-zero archive start offset (SFX)

The RAR reader SHALL accept an archive whose marker (`Rar!\x1a\x07\x00` for RAR4
or `Rar!\x1a\x07\x01\x00` for RAR5) begins at a non-zero byte offset — whether
supplied as an explicit start offset from detection (`payload_offset`) or
discovered by a bounded forward scan when the marker is absent at the open
position (forced `format=RAR` on an SFX stub).

The forced-format scan bound SHALL be the shared `SFX_MAX` constant (same
binding as the 7z parser and `detect_format`; today 2 MiB). The scan SHALL use
the same hit validator the detector uses. It SHALL return the earliest VALID
match, or if none validate the earliest identified candidate, so a damaged
payload still reaches the parser. After `MAX_VALIDATED_CANDIDATES` (256)
rejected candidates the scan SHALL stop and raise `CorruptionError` naming the
cap. That bound is structural (a real SFX stub does not carry hundreds of
format magics, and the parser has no `DetectionBudget`) and is not a
`ListingLimits` knob. A miss with no candidate SHALL raise `CorruptionError`
naming that there was no match.

Scanning both markers rather than their shared `Rar!\x1a\x07` prefix SHALL
resolve the version by which marker matched first.

Member and header offsets SHALL be relative to the resolved origin. The system
SHALL read in place and SHALL NOT copy the archive to a temporary file solely
to strip a stub.

#### Scenario: RAR SFX / start-offset matrix

| Case | Expected |
| --- | --- |
| Marker at open origin (offset 0) | Unchanged success path; version from the marker read |
| Forced `format=RAR`, marker at N within `SFX_MAX`, header validates | Scan finds N; members listed |
| Forced `format=RAR`, marker at N, header does not validate, no later VALID hit | Scan falls back to N; the parser reports the damage |
| Forced `format=RAR`, decoy magic then a VALID payload within `SFX_MAX` | Earliest VALID wins |
| Forced `format=RAR`, no marker within `SFX_MAX` | `CorruptionError` naming that there was no match |
| Forced `format=RAR`, `MAX_VALIDATED_CANDIDATES` (256) candidates rejected, none VALID | `CorruptionError` naming that the candidate cap was reached |

### Requirement: Bound RAR parser member tables at open

The native RAR header walk SHALL refuse to retain more members than
`listing_limits.max_members` when that field is not `None`, and SHALL raise
`ResourceLimitError` at parse (`open_archive`) when the ceiling is crossed.
`None` (`ListingLimits.UNLIMITED`) disables the bound. The config value is the
bound at parse because the table is built then; there is no separate
parser-constant ceiling. `stream_members()` / `streaming=True` are not an
escape hatch.

RAR has no header-size analogue for member count (the walk is sequential), so
`UNLIMITED` can walk until memory is exhausted. The parse bound is a member
count, not a byte budget. Spine `ListingLimits.max_metadata_bytes`
(`archive-reading`) still apply when members are registered into a
materialized list (`members()`), the same as every other format — not at
`open_archive`.

#### Scenario: RAR parser bound matrix

| Case | Expected |
| --- | --- |
| Hostile or honest archive over `listing_limits.max_members` | `ResourceLimitError` at parse (`open_archive`), including `stream_members()` / `streaming=True` |
| `listing_limits.max_members is None` (`UNLIMITED`) | No member-count bound at parse; a large honest archive opens |
| Default limits, typical archive | Open and listing succeed |

### Requirement: Expose RAR file-version history members

The system SHALL include RAR file-version history FILE blocks in the member list
instead of omitting them. RAR5 extra type `0x04` (and RAR3 `FILE_VERSION` when
present) identifies a prior revision. History members SHALL use the WinRAR /
`unrar` presented name `path;n` (version `n != 0`), set
`extra["rar.file_version"] = n`, and set `is_current=False`. The live revision of
the same archive path (no version extra, or version 0) SHALL keep the plain path
name and `is_current=True`.

`open` / `read` of a history `FILE` SHALL return that revision’s bytes. For
`unrar`-backed reads the backend SHALL request the exact presented member name
(`path;n`). Solid ALL-pipe demux SHALL pass `unrar`’s `-ver` switch when the
member list contains any versioned payload FILE so the pipe includes history
bytes in archive order; otherwise solid demux MAY omit `-ver`.

Default `extract` / `extract_all` SHALL skip history rows through the existing
`is_current=False` coordinator behavior (`safe-extraction`), recording each as
`ExtractionStatus.SUPERSEDED`. History rows have unique `path;n` presentation
names, so the shared last-entry-wins pass leaves their backend `is_current=False`
untouched. History rows SHALL count toward listing / parser member ceilings like
any other FILE.

#### Scenario: file-version matrix

| Case | Expected |
| --- | --- |
| RAR5 `-ver` archive with revisions 1..k then live path | Members include `path;1`…`path;k` (`is_current=False`) and `path` (`is_current=True`) |
| `read("path;1")` / `open` that member | Bytes of revision 1 |
| `read("path")` | Bytes of the live revision |
| `extract_all` default | Writes live `path` only; history rows `SUPERSEDED` |
| Solid archive that includes versioned payload FILEs | ALL-pipe demux uses `-ver`; stream order stays aligned |
| Nonsolid named `unrar p` of `path;n` | Exact member name; `-ver` not required |
| Hostile archive with many version rows | Rows count toward member caps |

### Requirement: Map RAR method bytes and unpack version

The RAR backend SHALL map the FILE-header method byte as follows:

| Method | `member.compression` |
| --- | --- |
| M0 (`0x30`) | `(CompressionMethod(algo=CompressionAlgorithm.STORED),)` |
| M1–M5 (`0x31`–`0x35`) | `(CompressionMethod(algo=CompressionAlgorithm.RAR, level=<1-5>),)` with `level` = method − `0x30` |
| Any other byte | `(CompressionMethod(algo=CompressionAlgorithm.UNKNOWN),)` — `level` omitted |

Ordinary RAR M1–M5 SHALL map to `CompressionAlgorithm.RAR`, not to any other
algorithm name. An unrecognized method byte SHALL not abort listing.

When the FILE header recorded an unpack version, every member — stored
included — SHALL set `extra["rar.extract_version"]` to that value. RAR3
copies the `UNP_VER` byte as stored, unvalidated. RAR5 members report `50`
(RAR5 records no per-file unpack version).

#### Scenario: RAR compression matrix

| Case | Expected |
| --- | --- |
| Stored member (M0) | `STORED`; `extra["rar.extract_version"]` present |
| Compressed member (M1–M5) | `RAR` with `level` 1–5; `extra["rar.extract_version"]` present |
| Method byte outside M0–M5 | `UNKNOWN`; `level` is `None`; listing succeeds |
| RAR3 FILE `UNP_VER` byte 200 | listing succeeds; `extra["rar.extract_version"]` is 200 |
| RAR5 member | `extra["rar.extract_version"]` is 50 |

### Requirement: Use RARLAB unrar only for member data that needs it

The system SHALL read stored, uncompressed, unencrypted members directly as raw
bytes through the shared pass-through backend. All other member data SHALL be
read by invoking a system RARLAB decompressor: `unrar` if a usable binary is on
`PATH`, otherwise `rar`. A usable binary is identified by a RARLAB banner
(`Alexander Roshal` or `RARLAB`, plus a standalone `UNRAR` or `RAR` token that
does not match inside `UNRAR`) whose parsed major.minor is 6.0 or later.
If a decompressor is required and missing or incompatible, the system SHALL raise
`PackageNotInstalledError` naming RARLAB `unrar` or `rar`. Archivey MUST NOT
silently use `unrar-free`, `unar`, `bsdtar`, `7z`, or a degraded backend. The
spawn SHALL be the `p` (print to stdout) command only.

#### Scenario: unrar dependency matrix

| Case | Expected |
| --- | --- |
| Stored member, `unrar`/`rar` missing | Raw bytes are returned without invoking either |
| Compressed member, both missing | `PackageNotInstalledError` names `unrar` or `rar` |
| PATH `unrar` is not RARLAB `unrar`, and no usable `rar` | `PackageNotInstalledError` names RARLAB `unrar` or `rar` |
| RARLAB `unrar` older than 6.0 and no usable `rar`, or a RARLAB banner with no parseable version | `PackageNotInstalledError` names the floor and the version found; refused at identification |
| RARLAB `rar` 6.0+ on `PATH`, `unrar` missing | Used for compressed/encrypted member data; spawn is `rar p` |
| RARLAB `unrar` 6.0+ and RARLAB `rar` both on `PATH` | `unrar` is used |
| Listing only, both missing | No data dependency is checked |

### Requirement: Constrain unrar argv by call site

The system SHALL invoke RARLAB `unrar` with member path arguments only as follows:

| Call site | Member path args after the archive |
| --- | --- |
| Solid `stream_members()` / solid `_iter_with_data` | none (unnamed `unrar p -inul <archive>`) |
| `_open_member` for a FILE member | exactly one archive-relative member path |
| Stored M0 unencrypted member | `unrar` not invoked |

The system MUST NOT pass multiple member paths, globs, or `@listfile` filters in this
capability’s initial implementation. Hardlink / file-copy members are never named on the
`unrar` command line; the shared link-following layer opens the target FILE instead.

#### Scenario: unrar argv matrix

| Case | Expected |
| --- | --- |
| Solid full or filtered `stream_members()` | One `unrar p` with no member path args |
| Nonsolid `open()` / lazy stream of a FILE | `unrar p … <archive> <member>` |
| `open()` on hardlink / `FILE_COPY` | `unrar` receives the target FILE path only (after link follow), or equivalent target open |
| Symlink member | No `unrar` data read for the link payload |

### Requirement: Stream solid RAR archives through one unrar pipe

For solid-archive `stream_members()`, the system SHALL run one
`unrar p -inul <archive>` subprocess **with no member path arguments** and
demultiplex stdout into per-member streams using the unpacked sizes of
**payload FILE members only** (members whose content `unrar p` emits).
Symlinks, hardlinks, file-copies, and directories MUST NOT consume pipe bytes
even when native headers advertise a non-zero size. Demultiplexing SHALL use
`SolidBlockReader` (or equivalent forward-only slicing). The system SHALL
validate each payload member's CRC32 or Blake2sp hash incrementally via the
shared verification stage. This path MUST process the archive once, not spawn
one subprocess per member.

#### Scenario: solid streaming matrix

| Case | Expected |
| --- | --- |
| `stream_members()` on a solid RAR | Exactly one unnamed `unrar p` process |
| Solid archive with symlinks / hardlinks | Pipe length equals Σ payload FILE sizes only; link members yield `stream is None` |
| Member has CRC32/Blake2sp | Verification runs as bytes are read and raises on mismatch |
| A later member is selected in the same pass | Earlier payload bytes are drained through the single pipe, not separate member subprocesses |
| Filtered `stream_members(selector)` | Still one unnamed `unrar p`; unselected payload tails are skipped via the shared iterator close/`SolidBlockReader` lazy skip |

### Requirement: Serve random access and extraction with bounded explicit temp use

The system SHALL serve non-solid random reads by invoking `unrar` for the target
member **with that member's path as the sole path argument**, doing O(member_size)
data work. For solid random reads, the system SHALL decode from archive start to
the target member (named `unrar p … <member>`) or extract once with `unrar x`
into an explicitly managed temporary directory and serve later reads from disk;
that directory is cleaned up on reader close. `extract_all()` MAY use one
`unrar x` to a temporary directory. Any temp materialization SHALL be a declared
RAR strategy, not an implicit in-memory buffer.

A non-path stream source SHALL NOT be copied to disk at open. Both stream shapes —
a single stream and an ordered set of stream volumes — SHALL defer the copy to the
first member read that `unrar` has to serve, and a caller that only lists SHALL
write nothing. An `open()` the reader refuses before spawning `unrar` — a name it
cannot address through an include mask, or one whose mask would pull in earlier
members — is not such a read and SHALL write nothing either. Listing SHALL be served from the source the caller supplied; for a
volume set the reader SHALL read each volume as its own bounded view over that
source rather than reopening or copying it.

When the copy does happen for a volume set it SHALL write the whole set, because
`unrar` resolves sibling volumes by name.

When the archive is opened from a
non-path stream source, `ar.cost.notes` SHALL include a human-readable disk-copy
caveat **at open** (path sources SHALL NOT): a single stream source SHALL warn
that reading a compressed member will copy the whole archive to disk; ordered
stream volumes SHALL warn that reading a compressed member will copy every volume
to a temp directory. The note is a
static open-time caveat, not an occurrence log:
it SHALL be present even if only stored members are read, and SHALL NOT appear
after materialization if it was absent at open. Mixed-password
nonsolid archives MUST NOT demultiplex one unnamed `unrar p` ALL pipe against the
full member list (wrong-password members are omitted from stdout and would
desynchronize sizes).

#### Scenario: random/extract matrix

| Case | Expected |
| --- | --- |
| Random `open()` in non-solid RAR | `unrar p … <archive> <member>`; work is O(member_size) |
| Repeated random opens in solid RAR | Backend may use one tempdir extraction and remove it on close |
| `extract_all()` | Backend may use one-shot `unrar x` |
| Mixed-password nonsolid stream/open | Per-member named `unrar` (or equivalent); no ALL-pipe demux |
| Single non-path stream, at open | `ar.cost.notes` warns a compressed read will copy to disk; nothing is written yet |
| Ordered stream volumes, at open | `ar.cost.notes` warns a compressed read will copy every volume; nothing is written yet |
| Ordered stream volumes, listing only | No temp directory is created |
| Ordered stream volumes, first compressed read | The whole set is written once; later reads reuse it; close removes it |
| Stream source, `open()` refused before any spawn | Nothing is written; the refusal raises without materializing |
| Path source | `ar.cost.notes` has no disk-copy caveat |

### Requirement: Support benchmark-gated small-member optimization

The reader SHALL allow an optional small-member optimization only when
benchmarks justify it. The optimization MAY build a temporary single-file RAR
containing the requested member and invoke `unrar` on that smaller archive when
the member is below a benchmark-derived threshold. Output MUST be byte-identical
to the direct `unrar` path. This optimization is **deferred**: the initial
native RAR reader MUST NOT implement it.

#### Scenario: small-member optimization matrix

| Case | Expected |
| --- | --- |
| Initial native RAR reader | Extract-hack / temp single-file RAR path is not used |
| Future enablement after benchmarks | Bytes match direct `unrar`; measured overhead is lower |
| Benchmark does not justify the threshold | Optimization is not used |

### Requirement: Report RAR cost and version-specific metadata

The system SHALL treat RAR solidity as a binary archive-level property because
RAR exposes no per-solid-block boundaries. `ArchiveInfo.is_solid` reflects the
native solid flag and `CostReceipt.solid_block_count` SHALL be `None`. Timestamp
mapping SHALL preserve RAR version semantics for `modified`, `accessed`, and
`created`: RAR4 local wall-clock timestamps become naive `datetime` values, and
RAR5 UTC/sub-second timestamps become timezone-aware UTC `datetime` values.
`accessed` and `created` come from the RAR5 `0x03` time extra (`HAS_ATIME` /
`HAS_CTIME`) or RAR3 EXTTIME; they SHALL be `None` when that extra or slot is
absent. When `created` is present and `host_os` is known, the member SHALL set
`extra["rar.created_is_ctime"]` to `True` for a Unix host (`host_os == 3`) and
`False` otherwise — a Unix RARLAB writer stores `st_ctime` (inode-change) in
the creation slot, not birth time. RAR5 Blake2sp-only members SHALL store the
digest bytes at `member.hashes["blake2sp"]` and omit `"crc32"`.

A **RAR5 redirect** member — symlink, hard link, or file copy — SHALL surface **no**
stored digest. Such a member keeps its target in a header field and stores no data
stream, so its CRC32 field covers zero bytes and RARLAB writes `crc32(b"") == 0`. That
value is correct about nothing: it does not describe the member (`size` is the target's
length while the digest covers no bytes) and is identical for every redirect member in
every archive. Surfacing it would make `member.hashes` mean something different in RAR
than in every other format, and the value it would carry is the one a de-duplicating
caller reads.

**RAR3/4 is the opposite and is unaffected**: it stores a symlink's target *as the
member's data*, so the stored CRC32 is a genuine digest of the target string — the same
thing ZIP and 7z record. The rule therefore keys on the RAR5 redirect, never on the
member type.

#### Scenario: metadata matrix

| Case | Expected |
| --- | --- |
| Solid RAR | `ArchiveInfo.is_solid` true; `solid_block_count is None` |
| RAR4 timestamp | `ArchiveMember.modified` is naive local wall-clock time |
| RAR5 timestamp | `ArchiveMember.modified` is timezone-aware UTC |
| RAR5 `-tsmca` archive | `modified` / `accessed` / `created` are timezone-aware UTC |
| RAR4 `-tsmca` archive | `modified` / `accessed` / `created` are naive local wall-clock |
| mtime-only archive (no atime/ctime extra) | `accessed` / `created` are `None`; `modified` populated |
| Unix-written member with `created` set | `extra["rar.created_is_ctime"] is True` |
| Win32-written member with `created` set | `extra["rar.created_is_ctime"] is False` |
| RAR5 member with Blake2sp only | `"blake2sp"` present as bytes; `"crc32"` absent |
| RAR5 symlink / hard link / file copy | `member.hashes` empty — never `crc32 == 0` |
| RAR4 symlink (target stored as data) | `"crc32"` present, equal to the target string's CRC32 |

### Requirement: Handle RAR5 redirect link types natively

The system SHALL read RAR5 link semantics from native `file_redir` metadata.
Hardlinks and file-copies (`RAR5_XREDIR_HARD_LINK`, `RAR5_XREDIR_FILE_COPY`)
SHALL be exposed as `MemberType.HARDLINK` with `link_target` set from the
redirect, so `ArchiveReader` link following returns the target FILE's data.
Unix symlinks and Windows symlinks/junctions (`RAR5_XREDIR_UNIX_SYMLINK`,
`RAR5_XREDIR_WINDOWS_SYMLINK`, `RAR5_XREDIR_WINDOWS_JUNCTION`) SHALL be
exposed as `MemberType.SYMLINK` with `link_target` from the redirect and
resolved by the format-independent link-following layer. Redirect members MUST
NOT appear in the solid `unrar p` demux size map.

#### Scenario: redirect matrix

| Case | Expected |
| --- | --- |
| RAR5 hardlink / `FILE_COPY` `open()` | Follows to target FILE data |
| RAR5 Unix/Windows symlink `open()` | Link following resolves target; symlink itself has no `unrar p` payload |
| Solid stream past a redirect member | Demux does not advance the pipe for that member |

### Requirement: Resolve RAR link targets when possible at list time

The system SHALL set `ArchiveMember.link_target` during member registration /
`_ensure_link_target` whenever the target is available without interactive input:

| Variant | Source of `link_target` |
| --- | --- |
| RAR5 symlink / Windows symlink / junction | native `file_redir` target string |
| RAR5 hardlink / `FILE_COPY` | native `file_redir` target string (`MemberType.HARDLINK`) |
| RAR4 Unix symlink | stored member bytes (direct read when M0 / readable without `unrar`) |

Encrypted link targets without a usable password MAY leave `link_target` unset and emit
the existing symlink-target diagnostic; listing MUST still succeed.

#### Scenario: link-target resolution matrix

| Case | Expected |
| --- | --- |
| RAR5 symlink | `type=SYMLINK`, `link_target` set from `file_redir` |
| RAR5 hardlink or `FILE_COPY` | `type=HARDLINK`, `link_target` set; `open()` follows to target data |
| RAR4 stored symlink | `link_target` equals stored target bytes decoded as text |
| Encrypted RAR4 symlink, no password | `link_target` may be unset; no crash on list |

### Requirement: Decrypt RAR5 header-encrypted archives natively

The system SHALL decrypt RAR5 header-encrypted archives through the optional
crypto backend when a valid password is supplied. The native parser derives the
AES key and decrypts headers itself; `unrar` is not required for listing and
remains required only for member data. Header-encrypted listing without a
password SHALL raise `EncryptionError`; with a password but no `cryptography`
backend (`[recommended]`), it SHALL raise `PackageNotInstalledError`. Any encrypted RAR
SHALL set `ArchiveInfo.is_encrypted` to `True`.

#### Scenario: header encryption matrix

| Case | Expected |
| --- | --- |
| Header-encrypted RAR5, no password | `EncryptionError` |
| Header-encrypted RAR5, password but no crypto backend | `PackageNotInstalledError` |
| Header-encrypted RAR5, valid password + crypto | Headers decrypt natively; members list; `is_encrypted` true |
| Read member data from that archive | `unrar` is still required |

### Requirement: Reject unsupported RAR variants clearly

Multi-volume RAR sets SHALL be supported by the volume contract, not rejected as
an unsupported variant. Opening a later volume before the first volume of a set
SHALL raise `UnsupportedFeatureError` (or a truncated/out-of-order error) rather
than silently mis-joining members. Legacy RAR 1.5 / 2.x archives MUST NOT be
rejected solely for extract version ≤ 20. Truly unreadable layouts (corrupt
headers, unknown required crypto without the extra) continue to raise typed
errors from their existing requirements.

#### Scenario: unsupported variant matrix

| Case | Expected |
| --- | --- |
| Multi-volume RAR4/RAR5 set is opened from volume 1 | Handled by the multi-volume requirement |
| Multi-volume set opened from a later volume first | `UnsupportedFeatureError` or truncated/out-of-order error |
| RAR 1.5 / 2.x archive is opened | Listing succeeds; not rejected for extract version |

### Requirement: Support multi-volume RAR sets

The system SHALL support multi-volume RAR archives named `name.partN.rar`
(RAR5/newer RAR4), including an SFX first volume named `name.partN.sfx` or
`name.partN.exe` beside later `.partN.rar` parts, or `name.rar` + `name.r00`,
`name.r01`, ... (older RAR4), including an old-scheme SFX first volume named
`name.exe` or `name.sfx` beside those `.rNN` parts (prefer `.rar` when more than
one first-volume name exists). The native parser SHALL read volume headers in
order and stitch members that span
volume boundaries into one logical member using continuation flags.
`open_archive()` SHALL accept either a path inside the set, with sibling
discovery in order, or an explicit ordered source sequence. For path sources,
data reads point `unrar` at the first volume so it can find later volumes. For
stream sources, data reads SHALL materialize ordered volumes for `unrar` when
needed. Missing or out-of-order volumes SHALL raise `UnsupportedFeatureError` or
a truncated error instead of a partial result.

#### Scenario: volume matrix

| Case | Expected |
| --- | --- |
| Open `name.part1.rar` with complete siblings | Headers across all volumes parse as one archive |
| Open `name.part1.sfx` with later `name.partN.rar` siblings | Same as partN: `.sfx` (or `.exe`) is volume 1; one logical archive |
| Open `name.rar` with `name.r00` / `name.r01` siblings | Same as partN: one logical archive; member data spans volumes |
| Open `name.exe` (or `name.sfx`) with `name.r00` siblings | Same as `.rar` + `.r00`: the SFX file is volume 1 |
| Read a member spanning volumes | Returned stream reassembles the member across boundaries |
| Open explicit ordered stream volumes | Metadata parses in order; data reads materialize volumes for `unrar` if needed |
| Missing or out-of-order volume | Error instead of partial or garbled output |

### Requirement: A malformed optional RAR5 extra record SHALL NOT refuse the archive

The RAR5 extra area of a FILE header is a list of optional records. The reader already
ignores a record whose type it does not recognise. A record whose type it *does*
recognise but whose body it cannot parse SHALL be treated the same way: the record is
dropped, the member is listed, and the walk continues with the next record.

CRC-32 on the enclosing header is an integrity check, not an authenticity one. A
well-formed extra still drops only the one malformed record; a crafted extra that
CRC-matches can produce one skip per byte, which is why the walk is capped below.

A dropped record SHALL leave the field it would have populated **absent, never wrong**.
Each record's parse SHALL commit its value only once every byte it needs has been read,
so a failure part-way through cannot leave a half-written timestamp, redirect or digest
behind.

Dropping a record SHALL NOT be silent: the reader SHALL emit
`MEMBER_HEADER_RECORD_SKIPPED` (see `diagnostics`) naming the member, the record and the
parse failure, attached to the member. Because that code is in `ARCHIVE_INTEGRITY_CODES`,
a caller who wants the archive refused instead SHALL get that from
`DiagnosticPolicy.strict()`.

A crafted extra area SHALL NOT retain one skipped record per attacker byte. The number of
dropped records retained per member is a structural cap (a handful of extras is every
well-formed FILE; more cannot be useful diagnostics). After the cap the extra-area walk
for that member stops, and stopping SHALL be reported: a caller SHALL be able to tell a
member whose records were all read from one whose header was abandoned part-way, because
how far to trust that member's metadata turns on it.

#### Scenario: A one-byte-short checksum record lists the member without a digest

- **GIVEN** a RAR5 archive whose BLAKE2sp extra record declares a size too small for the
  32-byte digest it contains, with a valid enclosing header CRC
- **WHEN** the archive is listed under the default diagnostic policy
- **THEN** the member SHALL be listed
- **AND** its BLAKE2sp hash SHALL be absent rather than a truncated or guessed value
- **AND** exactly one `MEMBER_HEADER_RECORD_SKIPPED` SHALL be attached to that member,
  with `record="hash"` and the record's numeric type
- **AND** `unrar` lists the same archive, which is why refusing it was wrong

#### Scenario: An unrecognised record type stays silent

- **WHEN** the extra area carries a record whose type the reader does not implement
- **THEN** the member SHALL be listed and **no** diagnostic SHALL be emitted
- **AND** this leniency SHALL NOT turn the pre-existing tolerance for unknown records
  into a diagnostic, or every archive written by a newer RAR would report one

#### Scenario: A zero-filled extra area does not retain one skip per byte

- **GIVEN** a RAR5 FILE extra area filled with zero bytes and a valid enclosing header CRC
- **WHEN** the archive is listed under the default diagnostic policy
- **THEN** the member SHALL be listed
- **AND** the number of `MEMBER_HEADER_RECORD_SKIPPED` diagnostics attached to it SHALL
  be at most the structural skip cap
- **AND** this cap exists because `xsize == 0` is one attacker byte per skip, which
  `max_members` cannot see
- **AND** exactly one of those diagnostics SHALL report that the walk stopped with the
  extra area unread, distinguishing it from a member whose records were all read

### Requirement: A malformed RAR5 encryption record SHALL remain fatal

The `FHEXTRA_CRYPT` record is the sole exception to the rule above. Dropping it would
leave the member's encryption parameters unset, and a member with no encryption
parameters is presented as plaintext. That is a *wrong* answer rather than a missing one,
and this library treats silently wrong metadata as its worst failure class.

A member whose encryption record cannot be parsed SHALL therefore raise, as it does
today. It SHALL NOT be listed as unencrypted, and it SHALL NOT be listed as encrypted
with absent parameters.

#### Scenario: An unparseable encryption record refuses the archive

- **GIVEN** a RAR5 member whose `FHEXTRA_CRYPT` record is truncated
- **WHEN** the archive is listed
- **THEN** listing SHALL raise `CorruptionError`
- **AND** the member SHALL NOT appear in any listing as an unencrypted member
### Requirement: Refuse a glob member name whose mask also matches earlier members

A RAR member's stored name may contain `*` or `?`. Because `unrar` is addressed by an
include mask, such a name can match other members, and `unrar` decompresses every match
and emits them concatenated ahead of the target.

When the mask built from a member's stored name also matches **earlier** payload
members, the system SHALL raise `UnsupportedFeatureError` rather than read the member.
The error SHALL name the number of bytes that would be decompressed first and the
configuration flag that allows the read, so the caller needs no source reading to
decide.

`ArchiveyConfig.rar_allow_glob_member_concatenation` SHALL default to `False`. When set
to `True` the read SHALL proceed and return the member's own bytes, skipping the earlier
matches as before. The flag SHALL govern only whether the read is attempted; it SHALL
NOT change what a successful read returns.

A glob name whose mask matches **no** other member SHALL be unaffected and SHALL read
without the flag.

A call site that builds no include mask SHALL be unaffected, whatever the member names
are. In particular a solid `stream_members()` pass uses one unnamed `unrar p` pipe
demultiplexed by size, so it SHALL read glob-named members without the flag.

The refusal exists because the extra decode is unbounded and unreported: it is not
covered by `ExtractionLimits`, which do not reach `open()` / `read()`, and
`AccessCost.DIRECT` does not predict it on a non-solid archive.

#### Scenario: glob member matrix

| Case | Expected |
| --- | --- |
| `a*.txt` with an earlier `subdir/aY.txt` match, default config | `UnsupportedFeatureError` naming the byte count and the flag |
| The same read with `rar_allow_glob_member_concatenation=True` | The member's own bytes, earlier matches skipped |
| `only*.dat`, whose mask matches nothing else, default config | Reads normally; no refusal |
| Solid `stream_members()` over glob-named members, default config | All members read; no mask is built |
| A name with no `*` or `?` | Unaffected in either configuration |
