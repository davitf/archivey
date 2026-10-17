## MODIFIED Requirements

### Requirement: Declare RAR format properties

The RAR backend SHALL expose these properties:

| Property | Value |
| --- | --- |
| Read dependency (metadata) | None; native RAR 1.5–RAR5 header parser |
| Read dependency (data) | RARLAB `unrar` or `rar` binary on `PATH` (`unrar` preferred), for compressed members and encrypted members archivey does not decrypt itself; `cryptography` for stored, encrypted members in one part |
| Listing cost | `REQUIRES_SCANNING`: a header walk at open, parsed natively, no member-data decompression. Also with a quick-open record, which saves seeks but not the walk |
| Access cost | `SOLID` for solid archives; `DIRECT` otherwise |
| Supports write | No |
| Requires seek | Yes |

#### Scenario: format property matrix

| Case | Expected |
| --- | --- |
| Open non-header-encrypted RAR without `unrar`/`rar` | Listing and metadata still work through the native parser |
| Read a stored, encrypted member in one part without `unrar`/`rar`, `cryptography` installed | Decrypted and returned by archivey |
| Open from a non-seekable source | Open fails because RAR header parsing requires seek |

### Requirement: Use RARLAB unrar only for member data that needs it

The system SHALL read stored, uncompressed, unencrypted members directly as raw bytes
through the shared pass-through backend, whatever the member's own solid flag says. A
member split across volumes SHALL be read by joining its parts in order. A stored,
encrypted member in one part SHALL be decrypted and read by archivey when
`cryptography` is installed: AES-128-CBC under the key and IV that the password and the
member's 8-byte salt give for RAR 2.9-4 (extract version 29 or later), AES-256-CBC
under the PBKDF2 key and the IV of the member's encryption record for RAR5. The
ciphertext is padded to whole 16-byte blocks; the read SHALL end at the member's
unpacked size and SHALL be checked against its stored digest, a RAR5 tweaked digest
included. The password SHALL be picked as for an `unrar` read: a RAR5 PswCheck rejects a
wrong one with `EncryptionError` before any byte is returned. Without `cryptography`
such a member SHALL be read by the selected program; when that program is missing too,
the `PackageNotInstalledError` SHALL name both `unrar` and `cryptography`. Each part of a
member split across volumes has its own encryption record, so a split encrypted member
is read by the program. All other
member data SHALL be read by invoking a system RARLAB decompressor: `unrar` if a usable
binary is on `PATH`, otherwise `rar`. A usable binary is identified by a RARLAB banner
(`Alexander Roshal` or `RARLAB`, plus a standalone `UNRAR` or `RAR` token that does not
match inside `UNRAR`) whose parsed major.minor is 6.0 or later. If a decompressor is
required and missing or incompatible, the system SHALL raise `PackageNotInstalledError`
naming RARLAB `unrar` or `rar`. Archivey MUST NOT use `unrar-free`, `bsdtar`, `7z`, or a
degraded backend. The spawn SHALL be the `p` (print to stdout) command only. This
requirement applies when `ArchiveyConfig.rar_decompressor` is `unrar`, or `auto` (the
default) with a usable RARLAB binary on `PATH`; `unar` is covered by `Read RAR member
data with unar`.

#### Scenario: unrar dependency matrix

| Case | Expected |
| --- | --- |
| Stored member, `unrar`/`rar` missing | Raw bytes are returned without invoking either |
| Stored member split across volumes, `unrar`/`rar` missing | Its parts are joined and returned, checked against the member's checksum |
| Stored member carrying its own solid flag (`rar -s -ms<ext>`), `unrar`/`rar` missing | Raw bytes are returned without invoking either |
| Stored, encrypted RAR 2.9-4 or RAR5 member in one part, `cryptography` installed | Decrypted by archivey, byte-identical to `unrar p`, seekable with `seekable_members=True`; no process starts |
| Same, RAR5, wrong password | `EncryptionError` from the PswCheck before any byte |
| Same, RAR 2.9-4, wrong password | Bytes end in a CRC mismatch (`CorruptionError`), as through `unrar`; an abandoned read emits `ENCRYPTED_MEMBER_UNVERIFIED` |
| Same, `cryptography` missing, `unrar` present | `unrar` reads it |
| Same, `cryptography` and `unrar` both missing | `PackageNotInstalledError` names `unrar` and `cryptography` |
| Stored, encrypted member split across volumes | Read by the program |
| Compressed member, both missing, and no `unar` (or `rar_decompressor="unrar"`) | `PackageNotInstalledError` names `unrar` or `rar` |
| PATH `unrar` is not RARLAB `unrar`, and no usable `rar` | `PackageNotInstalledError` names RARLAB `unrar` or `rar` |
| RARLAB `unrar` older than 6.0 and no usable `rar`, or a RARLAB banner with no parseable version | `PackageNotInstalledError` names the floor and the version found; refused at identification |
| RARLAB `rar` 6.0+ on `PATH`, `unrar` missing | Used for compressed member data and encrypted data archivey does not decrypt; spawn is `rar p` |
| RARLAB `unrar` 6.0+ and RARLAB `rar` both on `PATH` | `unrar` is used |
| Listing only, both missing | No data dependency is checked |
| Listing only, both missing, archive has a compressed RAR 1.5-4 comment | The comment is `None`; nothing else depends on a data program |

### Requirement: Constrain unrar argv by call site

The system SHALL invoke RARLAB `unrar` with member path arguments only as follows:

| Call site | Member path args after the archive |
| --- | --- |
| Solid `stream_members()` / solid `_iter_with_data` | none (unnamed `unrar p -inul <archive>`) |
| `_open_member` for a FILE member | exactly one archive-relative member path |
| Stored M0 unencrypted member | `unrar` not invoked |
| Stored M0 encrypted member in one part, `cryptography` installed | `unrar` not invoked |

The system MUST NOT pass multiple member paths, globs, or `@listfile` filters in this
capability’s initial implementation. Hardlink / file-copy members are never named on the
`unrar` command line; the shared link-following layer opens a hardlink's target FILE,
and the RAR reader opens a file copy's source FILE.

#### Scenario: unrar argv matrix

| Case | Expected |
| --- | --- |
| Solid full or filtered `stream_members()` | One `unrar p` with no member path args |
| Nonsolid `open()` / lazy stream of a FILE | `unrar p … <archive> <member>` |
| `open()` on hardlink / `FILE_COPY` | `unrar` receives the target (source) FILE path only, or equivalent target open |
| Symlink member | No `unrar` data read for the link payload |

### Requirement: Serve random access and extraction with bounded explicit temp use

The system SHALL serve non-solid random reads by invoking `unrar` for the target
member **with that member's path as the sole path argument**, doing
O(member_size) data work. For solid random reads, the system SHALL decode from
archive start to the target member (named `unrar p … <member>`). Each such read
is its own decode: the reader SHALL NOT amortize repeated solid reads by
extracting members into a temporary directory and serving later reads from disk.
`extract_all()` SHALL be served by the same `stream_members()` pass as any other
caller, plus a second `stream_members()` pass when a selected hardlink's source
was excluded and has to be re-read; on a solid archive each pass is one unnamed
`unrar p` addressed at the whole archive, decoding only as far as the last
member it needs. Which members a pass names on the `unrar` command line — and
which need no spawn at all — is governed by `Constrain unrar argv by call site`.
Any temp materialization SHALL be a declared RAR strategy, not an implicit
in-memory buffer; the only one the reader implements is copying a non-path
archive *source* to disk so `unrar` can seek it, the deferred small-member
optimization being the other strategy this capability declares.

A non-path stream source SHALL NOT be copied to disk at open. Both stream shapes —
a single stream and an ordered set of stream volumes — SHALL defer the copy to the
first member read that `unrar` has to serve, and a caller that only lists SHALL
write nothing. An `open()` the reader refuses before spawning `unrar` — a name it
cannot address through an include mask, or one whose mask would pull in earlier
members — is not such a read and SHALL write nothing either. Listing SHALL be served from the source the caller supplied; for a
volume set the reader SHALL read each volume as its own bounded view over that
source rather than reopening or copying it.

When the copy does happen for a volume set it SHALL write the whole set, because
`unrar` resolves sibling volumes by name. The copies SHALL be named in the set's own
scheme: `name.partN.rar`, or `name.rar`, `name.r00`, … for a RAR 1.5-2.x set whose
main header lacks the new-numbering flag, because `unar` looks for the next volume
only under that scheme.

The copy SHALL be bounded by `ArchiveyConfig.spool_limits` (`archive-reading`), measured
across the whole volume set, file volumes of a mixed set included. The archive size is
known before the copy, so an archive over `SpoolLimits.max_bytes` SHALL raise
`ResourceLimitError` before any byte is written and before
`unrar` is spawned. Where the size is not known up front, the copy SHALL stop before its
total passes the limit and SHALL remove what it wrote. The limit SHALL hold for the
reader, not for each attempt: once a copy has been refused, a later read that needs it
SHALL raise the same refusal without writing again. With `max_bytes=0` a member that
cannot be read directly SHALL be refused, and a member that can SHALL still read.

When the archive is opened from a non-path stream source, `ar.cost.notes` SHALL
include a human-readable disk-copy caveat **at open** (path sources SHALL NOT, except
the prefixed file that `Read RAR member data with unar` copies): a single stream source
SHALL warn that reading a member the program has to read (such as a compressed one)
will copy the whole archive to disk; ordered stream volumes SHALL warn that reading
such a member will copy every volume to a temp directory. The caveat SHALL name the
spool limit in force (or say there is none), so the caller reads the worst case at
open. When the limit already rules the copy out — `max_bytes=0`, or a copy whose size
is known at open and is over the limit — the caveat SHALL say that such a read will be
refused, in place of the copy warning. The note is a
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
| Repeated random opens in solid RAR | Each open is its own `unrar p` decode from archive start; no tempdir cache, and the re-decode is reported as `RewindWarning.min_redecode_bytes` |
| `extract_all()` | The same `stream_members()` pass as any other caller, plus a second pass when a selected hardlink's source was excluded and must be re-read; no `unrar x` |
| Mixed-password nonsolid stream/open | Per-member named `unrar` (or equivalent); no ALL-pipe demux |
| Single non-path stream, at open | `ar.cost.notes` warns a read the program serves (such as a compressed one) will copy to disk and names the spool limit; nothing is written yet |
| Ordered stream volumes, at open | `ar.cost.notes` warns a read the program serves will copy every volume and names the spool limit; nothing is written yet |
| Stream source at open, `max_bytes=0` or a known size over the limit | `ar.cost.notes` says a read the program serves will be refused and promises no copy |
| Ordered stream volumes, listing only | No temp directory is created |
| Solid `stream_members()` pass, no member read | Nothing is written, even from a stream source |
| Ordered stream volumes, first compressed read | The whole set is written once; later reads reuse it; close removes it |
| Stream source over `SpoolLimits.max_bytes` | `ResourceLimitError` naming the field; no temp file or directory; no `unrar` spawn |
| Volume set, each volume within the limit, total over it | `ResourceLimitError`; the limit weighs the total |
| Stream source of unknown size refused mid-copy, then another compressed read | The same refusal, with no second temp file |
| Stream source, `max_bytes=0` | Stored members read, unencrypted or decrypted by archivey; a member needing `unrar` is refused |
| `open()` on a directory-flagged header that declares data | Stored, unencrypted data is sliced by archivey and delivered; compressed or encrypted directory data raises `UnsupportedFeatureError` (`unrar p` emits nothing for a directory entry), never a spawn |
| Stream source, `open()` refused before any spawn | Nothing is written; the refusal raises without materializing |
| Path source | `ar.cost.notes` has no disk-copy caveat (under `unrar`); the spool limit never refuses it |
| Prefixed path source under `unar`, copy over `SpoolLimits.max_bytes` | `ar.cost.notes` says a compressed read will be refused; the read raises `ResourceLimitError` naming `rar_decompressor='unrar'`; no temp file |

### Requirement: Read RAR member data with unar

When `ArchiveyConfig.rar_decompressor` is `unar`, or `auto` with no usable RARLAB
`unrar` or `rar` on `PATH`, the system SHALL read compressed member data by invoking
`unar` 1.10 or later, identified on `PATH` by its `unar -h` banner with the same probe
timeout and stat-keyed cache as RARLAB `unrar`. An identified `unar` SHALL also decode a
small embedded RAR5 archive once, under the same timeout and cache, and SHALL NOT be
used unless it writes that archive's one member exactly and exits 0: Debian and Ubuntu
`unar` packages before 1.10.8+ds1-10 write nothing for such members, and their version
string does not tell them apart. A refused `unar` SHALL count as absent under `auto`,
and the refusal SHALL say what the check saw; it names the Debian patch only for exit 0
with a short member. Stored, unencrypted members SHALL still be read directly, whatever
their own solid flag, including a member split across volumes whose parts were all
found, and a stored, encrypted member in one part SHALL be decrypted by archivey as
under `unrar` (`Use RARLAB unrar only for member data that needs it`). The system MUST
NOT use `unrar` in that mode, and MUST NOT use `unar` in any other mode; a missing,
unidentified or refused `unar` SHALL raise
`PackageNotInstalledError` naming `unar`. `auto` SHALL choose once per reader, when the
archive opens; a read `unar` refuses MUST NOT be retried with `unrar`, and with neither
program present `auto` SHALL raise the `PackageNotInstalledError` that names RARLAB
`unrar` or `rar`. When `auto` chooses `unar`, `ar.cost.notes` SHALL say so at open,
naming the password exposure and the `unrar` setting that avoids it.

The argv SHALL be
`unar -o - -q -nr -k skip [-p <password>] [-i] -- <absolute path> [index …]`:
members named by decimal entry index in parse order, never by stored name. The
password is on the command line because `unar` takes it nowhere else, so other local
users can read it in the process list; the documentation SHALL say so. The native
RAR5 password check SHALL reject a wrong password before `unar` runs where the archive
stores one; otherwise, when `unar` produces no data for a non-empty encrypted member,
the read SHALL raise `EncryptionError`. The
system SHALL refuse with `UnsupportedFeatureError`, before spawning `unar`:

- an encrypted RAR 2.x-4.x member that archivey does not decrypt itself, and every
  member of a solid pass over such an archive (`unar` 1.10 returns no data for it, and
  exits 0, even with the right password);
- a member or solid pass that needs a password that is not ASCII, or contains NUL
  (`unar` 1.10 does not decrypt with it);
- every member of a multi-volume RAR5 set with encrypted headers (XADMaster 1.10.8
  returns no data for it, and exits 0);
- in a RAR5 solid archive, a member with data that follows an empty file, a
  directory or a link;
- a compressed member whose extract version is below 20 (RAR 1.5 algorithm);
- any member of a multi-volume set that has a prefix before the RAR.

A solid pass that includes a refused member SHALL name only the readable payload
members, so `unar` never decodes the refused one, and SHALL name at most 4000 of
them to stay inside `ARG_MAX`. A readable member past the 4000th SHALL be refused in
that pass with `UnsupportedFeatureError`; opening it on its own is not affected. A
single archive with a prefix SHALL be copied from the RAR's start before `unar`
reads it, bounded by `ArchiveyConfig.spool_limits` for a path source too, and
`ar.cost.notes` SHALL say so at open, naming the limit or the refusal as for a stream
source. Every
member read through `unar` SHALL be checked against its declared
size and stored digest, because `unar` exits 0 on some failures.

A compressed RAR 1.5-4 comment (a RAR 1.5/2.x old-style COMMENT subblock, or a
RAR 2.9-4 `CMT` SERVICE header that is not stored) SHALL be decoded by the selected
program, so with `unar` selected `unar` decodes it. The decoded text SHALL be used
only when its stored CRC matches (the CRC16 of a COMMENT subblock, the full CRC32 of a
`CMT` header); otherwise, or when the selected program is missing, the comment SHALL be
`None`, as it is with `unrar`. A compressed `CMT` that declares more than 256 KiB, the
most `rar` writes, SHALL be `None` and SHALL NOT be weighed or decoded.

#### Scenario: unar selection matrix

| Case | Expected |
| --- | --- |
| Default config (`auto`), RARLAB `unrar` present, compressed member | `unrar` is spawned; `unar` is not |
| Default config (`auto`), only a `unar` that passes the RAR5 check, compressed member | `unar` is spawned; `ar.cost.notes` says why at open |
| `rar_decompressor="unrar"`, only `unar` present | `PackageNotInstalledError` names RARLAB `unrar` or `rar`; `unar` is not used |
| `rar_decompressor="unar"`, `unar` missing, `unrar` present | `PackageNotInstalledError` names `unar`; `unrar` is not used |
| `rar_decompressor="auto"`, RARLAB `unrar` present | `unrar` is spawned; `unar` is not |
| `rar_decompressor="auto"`, only a `unar` that passes the RAR5 check | `unar` is spawned |
| Only a `unar` that fails the RAR5 check (`tests/test_unar_probe.py`), `auto` or `"unar"` | `auto`: as with neither present; `"unar"`: `PackageNotInstalledError` saying what the check saw |
| `rar_decompressor="auto"`, neither present | `PackageNotInstalledError` names RARLAB `unrar` or `rar` |
| `unar` selected, member name contains `*` | Read by index; no `rar_allow_glob_member_concatenation` needed |
| `unar` selected, encrypted RAR5 member, right password | Read correctly; the password is passed with `-p` |
| `unar` selected, encrypted RAR5 member, wrong password | `EncryptionError` |
| `unar` selected, encrypted RAR 2.x-4.x member, compressed or split | `UnsupportedFeatureError` naming the RAR 2.x-4.x reason |
| `unar` selected, stored encrypted RAR 2.9-4 member in one part, `open()` | Decrypted by archivey; `unar` is not spawned |
| `unar` selected, non-ASCII password | `UnsupportedFeatureError` naming the password reason |
| `unar` selected, RAR5 volume set with encrypted headers, right password | `UnsupportedFeatureError` |
| `unar` selected, RAR5 solid, empty file first | Members with data after it are refused; listing is not |
| `unar` selected, member before the first empty entry in a RAR5 solid pass | Read correctly from a run that names only readable members |
| `unar` selected, RAR 1.5 compressed member | `UnsupportedFeatureError` |
| `unar` selected, single archive after a 4 KiB prefix | Read from a copy that starts at the RAR; `ar.cost.notes` warns of the copy at open |
| `unar` selected, solid pass with a refused member and more than 4000 readable members | Members past the 4000th refused in the pass; each still opens on its own |
| `unar` selected, compressed RAR 1.5 archive comment | Decoded by `unar` and checked against its CRC16 |
| `unrar` present, RAR 2.9-4 archive comment in a compressed `CMT` SERVICE header | Decoded, checked against its CRC32, and UTF-16LE when `SUBHEAD_FLAGS_CMT_UNICODE` is set |

### Requirement: Read RAR without any external program

When `ArchiveyConfig.rar_decompressor` is `none`, the system MUST NOT start `unrar`,
`rar` or `unar` for that reader: not to identify them at open, not to decode a comment,
and not to read member data. Opening and listing SHALL work as with any other setting,
including header-encrypted archives given the right password. A stored member that is
not encrypted SHALL be read directly, as it is under every setting, when all of its
parts were found, and a stored, encrypted member in one part SHALL be decrypted by
archivey, as under every setting; without `cryptography` its read SHALL raise
`PackageNotInstalledError` naming `cryptography`. A member with a part in a missing
volume SHALL raise `TruncatedError`, as under every setting ("A set with volumes
missing SHALL list what it has, then
raise"): no program can read data that is not there. Every other member read SHALL raise
`UnsupportedFeatureError` before any process starts or any source is copied, naming why
the member cannot be read (compressed, or encrypted in a form archivey does not decrypt)
and the `unrar` setting that reads it. A compressed RAR 1.5-4 comment SHALL be `None`. When
the archive has a file member that this setting refuses, `ar.cost.notes` SHALL say so at
open.

#### Scenario: no external program matrix

| Case | Expected |
| --- | --- |
| Non-solid archive, stored plaintext members, path or stream source | Every member reads; no process starts; `ar.cost.notes` is empty |
| Compressed member | `UnsupportedFeatureError` naming "compressed"; no process starts |
| Compressed encrypted member | `UnsupportedFeatureError` naming "compressed" |
| Stored encrypted member in one part, right password | Decrypted and read; no process starts; `ar.cost.notes` is empty when nothing else is refused |
| Stored encrypted member in one part, `cryptography` missing | `PackageNotInstalledError` naming `cryptography`; `ar.cost.notes` says reads will be refused |
| Stored encrypted member split across volumes | `UnsupportedFeatureError` naming "encrypted" |
| Stored member split across volumes | Its parts are joined and read; no process starts |
| Stored member split across volumes, its first or a later part missing | `TruncatedError`, as under every setting; no process starts |
| Stored member with its own solid flag | Read directly; no process starts |
| Solid archive, `stream_members()` | Each member's read is refused on its own; no solid pass starts |
| Header-encrypted archive, right password | Lists; no process starts |
| Compressed RAR 1.5 archive comment, or a compressed RAR 2.9-4 `CMT` | `None` |

### Requirement: Password lists for data with no password check

RAR3/4 file data records no password check value, and neither does a RAR5 encryption
record without a usable PswCheck. When the caller gives more than one distinct password
(or a provider) for an archive whose headers are not encrypted, the reader SHALL judge
the candidates for such a member by decoding it, under the shared confirmation rule
(`archive-reading`, "Confirm candidates when a weak check permits retries"), before the
member's own read starts:

- the **bounded probe** reads at most `PASSWORD_CONFIRM_PREFIX_BYTES` of the member's
  output from the decompressor; a program error, a wrong-password exit or output that
  ends short SHALL reject the candidate. A member that fits the prefix is checked against
  its CRC, which confirms;
- the **full check** reads the whole member and compares its CRC;
- a **stored** RAR 2.9+ or RAR5 member in one part that records a CRC32 SHALL be judged
  by decrypting it natively (`Use RARLAB unrar only for member data that needs it`) and
  comparing its CRC, a RAR5 tweaked CRC after the same transform under the candidate's
  HashKey, with no external program: every wrong key decrypts a stored member to bytes
  of the right length, so only the CRC can tell. The member's own read is then also
  native.

In a non-solid archive each such member is judged on its own data. In a solid archive,
and for the pass over a whole archive, the candidates are judged once, on the first
encrypted member (solid) or the smallest one (non-solid). One distinct password, and the
header password of a header-encrypted archive, SHALL go to the decompressor unjudged, as
before. A member whose password was confirmed against its own CRC SHALL NOT emit
`ENCRYPTED_MEMBER_UNVERIFIED` when its read is abandoned.

#### Scenario: RAR3/4 password list matrix

| Case | Expected |
| --- | --- |
| Non-solid, compressed, `password=[wrong, right]`, member larger than the prefix | Every member reads correctly |
| Same, `wrong` survives the 64 KiB prefix | The full check rejects it; every member reads correctly |
| Solid, compressed, `password=[wrong, right]` | One resolution on the first encrypted member; every member reads, by `open()` and `stream_members()` |
| Stored encrypted member, `password=[wrong, right]` | Judged natively by CRC; no process started for the check |
| `password=[wrong1, wrong2]` | `EncryptionError` |
| One password | Handed to the decompressor unjudged, as before |
