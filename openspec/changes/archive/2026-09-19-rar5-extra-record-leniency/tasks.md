# Tasks — RAR5 extra-record leniency

## 1. The diagnostic

- [x] 1.1 Add `DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED` and
      `MemberHeaderRecordContext` (`kind="member_header_record"`, `archive_name`,
      `member_name`, `member_id`, `record`, `record_id`, `reason`). `record` is the
      format's own name for the record; `record_id` is its numeric type, so a record the
      name map does not cover is still identifiable.
- [x] 1.2 Register the pairing in `_CODE_CONTEXT_KINDS`, add the variant to the
      `DiagnosticContext` union, and re-export the context from `archivey/__init__.py`.
- [x] 1.3 Add the code to `ARCHIVE_INTEGRITY_CODES`, so `DiagnosticPolicy.strict()`
      raises on it and today's callers can keep the refuse-the-archive behaviour.

## 2. The parser

- [x] 2.1 In `_parse_rar5_file_block`, wrap each known extra-record branch so a
      `CorruptionError` drops that record and the walk continues. A record too short to
      carry its own type vint is dropped the same way, recorded as `unknown`.
- [x] 2.2 Keep `_RAR5_XFILE_ENCRYPTION` fatal — re-raise rather than record. Skipping it
      would list an encrypted member as plaintext.
- [x] 2.3 Carry the drops on `RarMemberInfo.skipped_header_records` as
      `(record_name, record_type, reason)`, defaulting to the shared empty tuple.

## 3. The reader

- [x] 3.1 Emit one `MEMBER_HEADER_RECORD_SKIPPED` per dropped record from
      `RarReader._emit_member_diagnostics`, with `attach_to_member=True` so it reaches
      `member.diagnostics` and can raise before `_to_member` returns.

## 4. Tests

- [x] 4.1 A committed fixture whose BLAKE2sp record is one byte short: the member lists,
      `blake2sp_hash` is `None` rather than wrong, and exactly one
      `MEMBER_HEADER_RECORD_SKIPPED` is attached to it with `record="hash"`,
      `record_id=2`.
- [x] 4.2 The same fixture under `DiagnosticPolicy.strict()` raises
      `DiagnosticRaisedError`, so the old behaviour stays reachable.
- [x] 4.3 A malformed *encryption* record still raises `CorruptionError`, and the member
      is not listed as plaintext.
- [x] 4.4 An unknown record type still lists silently — no diagnostic — so the leniency
      did not turn the existing tolerance into noise.
- [x] 4.5 `unrar l` lists the same fixture, recorded as the oracle for why the refusal
      was wrong.

## 5. Docs

- [x] 5.1 `dev-docs/formats/rar.md` §6: record the posture and the encryption exception,
      so the leniency question is not re-derived. Same question `open-issues.md` **P3**
      asks about tar — cross-reference it rather than answering it there.
- [x] 5.2 `docs/` diagnostics reference: add the new code to the table.
