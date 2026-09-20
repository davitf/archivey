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

A crafted extra area SHALL NOT retain one skipped record per attacker byte. The number of
dropped records retained per member is a structural cap (a handful of extras is every
well-formed FILE; more cannot be useful diagnostics). After the cap the extra-area walk
for that member stops.

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
