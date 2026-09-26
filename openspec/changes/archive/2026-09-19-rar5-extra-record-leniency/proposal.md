# A malformed optional RAR5 header record drops the record, not the archive

## Why

The RAR5 extra area is a list of optional records hanging off a FILE header: a checksum,
timestamps, a redirect target, a version number, an owner. `_parse_rar5_file_block` walks
it and **ignores a record type it does not recognise** — the loop ends with
`# OWNER / SERVICE / unknown: ignore`, and it tolerates a byte of trailing padding "like
rarfile". But a record type it *does* recognise and cannot parse raises `CorruptionError`
out of the walk, out of `_parse_rar5`, and out of `open_archive`. Every member is refused,
including the ones that parsed.

That inconsistency is not a chosen posture. Two hundred lines up,
`_rar5_locator_qopen_abs` wraps the identical `load_vint(xdata, 0)` in a `try` and
`continue`s.

Measured on `4ef5c98`: a normal `rar a -m3 x.rar f1.txt` (139 bytes), with **one byte**
changed — the BLAKE2sp record's `xsize` vint from 34 to 3, so the record still fits inside
the header but its own 32-byte read runs off the end — and the header CRC recomputed so the
block is otherwise valid:

```
$ unrar l x_trunc.rar
 -rw-r--r--     20000  ????-??-?? ??:??  f1.txt        # lists, exit 0

>>> archivey.open_archive("x_trunc.rar")
CorruptionError: Unexpected EOF while reading bytes
```

`unrar` 7.00 degrades — it drops the timestamp it can no longer locate and lists the
member. archivey refuses the archive, over a *checksum*, with a message that names no
member, no field and no offset.

Found by the #315 sweep (batch S16, finding R2-K7) in the first agent read of
`rar_parser.py`.

## What Changes

- A malformed **optional** RAR5 extra record is dropped and the member is listed without
  whatever it carried. The field keeps the value it already had; nothing half-written is
  committed.
- Dropping one is **not silent**: it emits a new `MEMBER_HEADER_RECORD_SKIPPED`
  diagnostic naming the member, the record and the parse failure, attached to the member.
- The new code joins `ARCHIVE_INTEGRITY_CODES`, so `DiagnosticPolicy.strict()` raises on
  it. **That is what makes this change safe rather than merely lenient**: a caller who
  wants today's refuse-the-archive behaviour keeps it by asking for strictness, and the
  default stops discarding good members over a bad checksum record.
- **The encryption record is deliberately excluded and stays fatal.** Skipping it would
  leave `file_encryption` unset, so an encrypted member would list as plaintext — a wrong
  answer rather than a missing one, which is the failure class this library ranks worst. A
  member whose encryption parameters cannot be read is not a member that can be presented.

## Impact

- **Affected specs:** `diagnostics` (one new code, context variant and integrity-set
  membership), `format-rar` (the leniency rule and its one exception).
- **Affected code:** `archivey/diagnostics.py`, `archivey/__init__.py`,
  `internal/backends/rar_parser.py`, `internal/backends/rar_reader.py`.
- **Behaviour change, in the lenient direction:** archives that raised `CorruptionError`
  now list. Nothing that listed before stops listing, and no field changes value — a
  dropped record leaves its field absent, never wrong. A `default=RAISE` policy gains a
  code it did not have, which is the taxonomy-growth case `DiagnosticPolicy.strict()`
  already exists to absorb.
- **Per-member footprint:** `RarMemberInfo` gains one slot, defaulting to the shared empty
  tuple. That matters here because #353's listing-bound argument rests on a measured
  per-member size; one pointer against ~363 B does not move it.
