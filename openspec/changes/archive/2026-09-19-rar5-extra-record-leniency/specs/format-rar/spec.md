# format-rar — a malformed optional extra record drops the record, not the archive

## ADDED Requirements

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

A crafted extra area SHALL NOT retain one skipped record per attacker byte, nor cost one
parse per attacker byte. The number of dropped records retained per member is a structural
cap (a handful of extras is every well-formed FILE; more cannot be useful diagnostics).
After the cap the extra-area walk for that member stops, and stopping SHALL be reported: a
caller SHALL be able to tell a member whose records were all read from one whose header
was abandoned part-way, because how far to trust that member's metadata turns on it.

The line SHALL fall between a record's *framing* and its *body*, because that is where the
information is. A body the reader cannot parse costs one record and leaves the next
record's offset known, so the walk continues. A size vint the reader cannot use costs every
later record, so the walk stops and reports that it stopped. A size is unusable when it
cannot be read at all, when it runs past the header, or when it is below the minimum a
record can have: a record's body opens with its type vint, so **one byte is the smallest
legal record** — a type with no payload, which is what an unimplemented record looks like —
and a declared size of zero names nothing while still advancing the cursor, which is what
made one attacker byte cost one retained record.

`unrar` 7.00 does continue past a zero-size record, and is wrong for it: one such record in
front of an encrypted member's records makes `unrar l` lose both the encryption record and
the timestamp and list the member as plaintext. The oracle that justifies the leniency
above SHALL NOT be read as justifying this.

#### Scenario: A record whose size cannot be used stops the walk

- **GIVEN** a RAR5 FILE extra area whose first record declares a size of zero, or a size
  larger than what remains of the header, or whose size vint has no terminating byte, with
  a valid enclosing header CRC
- **WHEN** the archive is listed under the default diagnostic policy
- **THEN** the walk SHALL stop at that record rather than trying to resynchronise
- **AND** the member SHALL be reported as having had its header cut short, not listed as
  though its extra area had been read to the end

#### Scenario: A record whose body cannot name its type is dropped, not fatal

- **GIVEN** a RAR5 FILE extra record declaring a one-byte body that holds only a vint
  continuation byte, sitting in front of the member's `FHEXTRA_CRYPT` record
- **WHEN** the archive is listed under the default diagnostic policy
- **THEN** the member SHALL be listed with one `MEMBER_HEADER_RECORD_SKIPPED`
- **AND** the member SHALL still be reported as encrypted, because the record's size is
  usable and the walk goes on to reach the encryption record
- **AND** nothing SHALL report the header as cut short

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
