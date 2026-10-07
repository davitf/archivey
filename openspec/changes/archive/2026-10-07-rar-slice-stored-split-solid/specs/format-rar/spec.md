## MODIFIED Requirements

### Requirement: Use RARLAB unrar only for member data that needs it

The system SHALL read stored, uncompressed, unencrypted members directly as raw
bytes through the shared pass-through backend, whatever the member's own solid flag
says. A member split across volumes SHALL be read by joining its parts in order.
All other member data SHALL be read by invoking a system RARLAB decompressor: `unrar` if a usable binary is on
`PATH`, otherwise `rar`. A usable binary is identified by a RARLAB banner
(`Alexander Roshal` or `RARLAB`, plus a standalone `UNRAR` or `RAR` token that
does not match inside `UNRAR`) whose parsed major.minor is 6.0 or later.
If a decompressor is required and missing or incompatible, the system SHALL raise
`PackageNotInstalledError` naming RARLAB `unrar` or `rar`. Archivey MUST NOT
use `unrar-free`, `bsdtar`, `7z`, or a degraded backend. The
spawn SHALL be the `p` (print to stdout) command only. This requirement applies
when `ArchiveyConfig.rar_decompressor` is `unrar`, or `auto` (the default) with a
usable RARLAB binary on `PATH`; `unar` is covered by `Read RAR member data with unar`.

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
`rar` or `unar` for that reader: not to identify them at open, not to decode a
comment, and not to read member data. Opening and listing SHALL work as with any
other setting, including header-encrypted archives given the right password. A
stored member that is not encrypted SHALL be read directly, as it is under every
setting, when all of its parts were found. Every other member read SHALL raise
`UnsupportedFeatureError` before any process starts or any source is copied,
naming why the member cannot be read (compressed, encrypted, or split across volumes
with a part missing) and the `unrar` setting that reads it. A compressed RAR 1.5/2.x old-style comment
SHALL be `None`. When the archive has a file member that this setting refuses,
`ar.cost.notes` SHALL say so at open.

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
