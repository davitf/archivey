## MODIFIED Requirements

### Requirement: Use RARLAB unrar only for member data that needs it

The system SHALL read stored, uncompressed, unencrypted members directly as raw bytes
through the shared pass-through backend, whatever the member's own solid flag says. A
member split across volumes SHALL be read by joining its parts in order. All other
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
| Compressed member, both missing, and no `unar` (or `rar_decompressor="unrar"`) | `PackageNotInstalledError` names `unrar` or `rar` |
| PATH `unrar` is not RARLAB `unrar`, and no usable `rar` | `PackageNotInstalledError` names RARLAB `unrar` or `rar` |
| RARLAB `unrar` older than 6.0 and no usable `rar`, or a RARLAB banner with no parseable version | `PackageNotInstalledError` names the floor and the version found; refused at identification |
| RARLAB `rar` 6.0+ on `PATH`, `unrar` missing | Used for compressed/encrypted member data; spawn is `rar p` |
| RARLAB `unrar` 6.0+ and RARLAB `rar` both on `PATH` | `unrar` is used |
| Listing only, both missing | No data dependency is checked |
| Listing only, both missing, archive has a compressed RAR 1.5/2.x comment | The comment is `None`; nothing else depends on a data program |

### Requirement: A malformed RAR5 encryption record SHALL remain fatal

The `FHEXTRA_CRYPT` record is the sole exception to the rule above. Dropping it would
leave the member's encryption parameters unset, and a member with no encryption
parameters is presented as plaintext. That is a *wrong* answer rather than a missing one,
and this library treats silently wrong metadata as its worst failure class.

A member whose encryption record cannot be parsed SHALL therefore raise, as it does
today. It SHALL NOT be listed as unencrypted, and it SHALL NOT be listed as encrypted
with absent parameters.

A member whose extra-area walk stopped before the end of the area SHALL be reported as
**encrypted**, whether or not an encryption record was read. The walk may have stopped in
front of one, so reporting such a member as unencrypted is the same wrong answer reached
by omission rather than by dropping anything, and the diagnostic saying the header was cut
short does not change what the field says. This is the one place the sentence above gives
way and the member carries no parameters.

"Encrypted" and "we could not tell" SHALL nonetheless remain distinguishable inside the
backend, because they are acted on differently. The cut-short answer SHALL apply to the
member's own reported flag and SHALL NOT reach the archive-level one: `ArchiveInfo`
reports header-level encryption and the aggregate of members *known* to be encrypted, so
one damaged member SHALL NOT make a wholly plaintext archive report as encrypted, hand the
caller's password to `unrar`, or relabel an empty read as a wrong password.

The cost of failing closed falls on the member's direct read. A stored member is otherwise
sliced straight from the source; one whose header was cut short SHALL NOT be handed back
unchecked, because those bytes are ciphertext if the record the walk never reached was the
encryption record.

A checksum that survived the damage SHALL settle it. RAR5 keeps CRC32 in the fixed FILE
header and BLAKE2sp in the extra area, so which digest a cut leaves behind is the writer's
choice, not archivey's. Where one survives, the member's stored bytes SHALL be verified
against it **before** any byte is returned, and the member SHALL be readable when they
match — on any installation, with or without `unrar`. Where none survives, the member SHALL
NOT be readable. The refusal SHALL name the cut-short header rather than the missing
package: installing `unrar` is a way out, not the cause, and it is not a better-informed
one — measured on unrar 7.00 it reads the same damaged header and reaches the same wrong
conclusion, applying that same digest test and returning the bytes unverified when no
digest survived.

The check SHALL run before the first byte is returned rather than at end of stream, for the
reason the ZIP ZipCrypto stored path gives: nothing in a stored member's framing can reject
wrong bytes incrementally, so a caller that stops reading early would never reach an
end-of-stream verdict. Its cost is one extra pass over an already-damaged member and none
at all on an undamaged one.

**Both paragraphs above are scoped to the member archivey reads by slicing the source** —
stored, including one split across volumes or carrying its own solid flag. Every
other cut-short member is decoded by
`unrar`, which is handed the whole member and cannot be asked to check a digest first;
there any surviving digest is verified as it is for an undamaged member, at end of stream,
and a member with none is read with nothing checking it. That is unchanged behaviour and
not a guarantee this requirement makes. Such a member SHALL still be reported as encrypted
and SHALL still carry the cut-short diagnostic, so a strict policy refuses it.

The diagnostic reporting a cut-short header SHALL name the fault that ended the walk. Four
different faults end it — the skip cap, a size that cannot be read, a size that overruns
the area, and a size below the one-byte minimum — and they are not interchangeable: this
message is the only thing that explains why a member may be reported encrypted when
nothing else in its listing says so.

#### Scenario: A cut-short header never reports an encrypted member as plaintext

- **GIVEN** a RAR5 member whose `FHEXTRA_CRYPT` record is preceded by enough records to
  stop the walk — past the skip cap, or one whose size cannot be used
- **WHEN** the archive is listed under the default diagnostic policy
- **THEN** the member SHALL be reported as encrypted
- **AND** a member whose extra area *was* read to the end SHALL NOT be reported as
  encrypted merely for having dropped a record, because that question was asked and
  answered

#### Scenario: One cut-short member does not report the archive as encrypted

- **GIVEN** a RAR5 archive with nothing encrypted in it, one of whose members has a
  cut-short extra area
- **WHEN** the archive is listed
- **THEN** that member SHALL be reported as encrypted
- **AND** the archive SHALL NOT be reported as encrypted

#### Scenario: A surviving checksum settles a cut-short stored member

- **GIVEN** a stored, unencrypted RAR5 member whose extra-area walk stopped early, whose
  CRC32 is in the fixed FILE header and so survived the damage
- **WHEN** it is read on an installation with no RARLAB `unrar` or `rar` available
- **THEN** the member SHALL be read and its content returned
- **AND** the member SHALL still be reported as encrypted, its header having never settled
  the question

#### Scenario: A cut-short stored member whose bytes fail the surviving checksum

- **GIVEN** a stored, *encrypted* RAR5 member whose extra-area walk stopped before its
  `FHEXTRA_CRYPT` record, whose CRC32 survived the damage
- **WHEN** it is read
- **THEN** the read SHALL raise `CorruptionError` reporting that the stored bytes do not
  match the surviving checksum
- **AND** no ciphertext SHALL be returned as member content

#### Scenario: A cut-short stored member names the header, not the missing package

- **GIVEN** a stored RAR5 member whose extra-area walk stopped early and whose only digest
  was BLAKE2sp, which the same cut destroyed
- **WHEN** it is read on an installation with no RARLAB `unrar` or `rar` available
- **THEN** the read SHALL raise `CorruptionError` naming the cut-short header and the
  absence of a surviving checksum

#### Scenario: An unparseable encryption record refuses the archive

- **GIVEN** a RAR5 member whose `FHEXTRA_CRYPT` record is truncated
- **WHEN** the archive is listed
- **THEN** listing SHALL raise `CorruptionError`
- **AND** the member SHALL NOT appear in any listing as an unencrypted member

### Requirement: Read RAR without any external program

When `ArchiveyConfig.rar_decompressor` is `none`, the system MUST NOT start `unrar`,
`rar` or `unar` for that reader: not to identify them at open, not to decode a comment,
and not to read member data. Opening and listing SHALL work as with any other setting,
including header-encrypted archives given the right password. A stored member that is
not encrypted SHALL be read directly, as it is under every setting, when all of its
parts were found. Every other member read SHALL raise `UnsupportedFeatureError` before
any process starts or any source is copied, naming why the member cannot be read
(compressed, encrypted, or split across volumes with a part missing) and the `unrar`
setting that reads it. A compressed RAR 1.5/2.x old-style comment SHALL be `None`. When
the archive has a file member that this setting refuses, `ar.cost.notes` SHALL say so at
open.

#### Scenario: no external program matrix

| Case | Expected |
| --- | --- |
| Non-solid archive, stored plaintext members, path or stream source | Every member reads; no process starts; `ar.cost.notes` is empty |
| Compressed member | `UnsupportedFeatureError` naming "compressed"; no process starts |
| Encrypted member, stored or compressed | `UnsupportedFeatureError` naming "encrypted" |
| Stored member split across volumes | Its parts are joined and read; no process starts |
| Stored member split across volumes, a later part missing | `UnsupportedFeatureError` naming "not every part was found" |
| Stored member with its own solid flag | Read directly; no process starts |
| Solid archive, `stream_members()` | Each member's read is refused on its own; no solid pass starts |
| Header-encrypted archive, right password | Lists; no process starts |
| Compressed RAR 1.5 archive comment | `None` |

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

When the archive is opened from a
non-path stream source, `ar.cost.notes` SHALL include a human-readable disk-copy
caveat **at open** (path sources SHALL NOT, except the prefixed file that
`Read RAR member data with unar` copies): a single stream source SHALL warn
that reading a compressed member will copy the whole archive to disk; ordered
stream volumes SHALL warn that reading a compressed member will copy every volume
to a temp directory. The caveat SHALL name the spool limit in force (or say there is
none), so the caller reads the worst case at open. When the limit already rules the copy
out — `max_bytes=0`, or a copy whose size is known at open and is over the limit — the
caveat SHALL say that such a read will be refused, in place of the copy warning. The
note is a
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
| Single non-path stream, at open | `ar.cost.notes` warns a compressed read will copy to disk and names the spool limit; nothing is written yet |
| Ordered stream volumes, at open | `ar.cost.notes` warns a compressed read will copy every volume and names the spool limit; nothing is written yet |
| Stream source at open, `max_bytes=0` or a known size over the limit | `ar.cost.notes` says a compressed read will be refused and promises no copy |
| Ordered stream volumes, listing only | No temp directory is created |
| Solid `stream_members()` pass, no member read | Nothing is written, even from a stream source |
| Ordered stream volumes, first compressed read | The whole set is written once; later reads reuse it; close removes it |
| Stream source over `SpoolLimits.max_bytes` | `ResourceLimitError` naming the field; no temp file or directory; no `unrar` spawn |
| Volume set, each volume within the limit, total over it | `ResourceLimitError`; the limit weighs the total |
| Stream source of unknown size refused mid-copy, then another compressed read | The same refusal, with no second temp file |
| Stream source, `max_bytes=0` | Stored, unencrypted members read; a member needing `unrar` is refused |
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
found. The system MUST NOT use `unrar` in that mode, and MUST NOT use `unar` in any
other mode; a missing, unidentified or refused `unar` SHALL raise
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

- an encrypted RAR 2.x-4.x member, and every member of a solid pass over such an
  archive (`unar` 1.10 returns no data for it, and exits 0, even with the right
  password);
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

A compressed RAR 1.5/2.x old-style comment SHALL be decoded by the selected
program, so with `unar` selected `unar` decodes it. The decoded text SHALL be used
only when its stored CRC16 matches; otherwise, or when the selected program is
missing, the comment SHALL be `None`, as it is with `unrar`.

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
| `unar` selected, encrypted RAR 2.x-4.x member | `UnsupportedFeatureError` naming the RAR 2.x-4.x reason |
| `unar` selected, non-ASCII password | `UnsupportedFeatureError` naming the password reason |
| `unar` selected, RAR5 volume set with encrypted headers, right password | `UnsupportedFeatureError` |
| `unar` selected, RAR5 solid, empty file first | Members with data after it are refused; listing is not |
| `unar` selected, member before the first empty entry in a RAR5 solid pass | Read correctly from a run that names only readable members |
| `unar` selected, RAR 1.5 compressed member | `UnsupportedFeatureError` |
| `unar` selected, single archive after a 4 KiB prefix | Read from a copy that starts at the RAR; `ar.cost.notes` warns of the copy at open |
| `unar` selected, solid pass with a refused member and more than 4000 readable members | Members past the 4000th refused in the pass; each still opens on its own |
| `unar` selected, compressed RAR 1.5 archive comment | Decoded by `unar` and checked against its CRC16 |
