## MODIFIED Requirements

### Requirement: Map ZIP member metadata to ArchiveMember

The ZIP backend SHALL map each `ZipInfo` to `ArchiveMember` with these field
rules:

| Field | Mapping |
| --- | --- |
| `mode` | `external_attr >> 16` only for Unix entries with non-zero attrs; otherwise `None` |
| timestamps | DOS `date_time` base (naive local wall-clock, 2s granularity, 1980 sentinel → `None`); NTFS extra `0x000A` UTC FILETIMEs override present fields; Extended Timestamp `0x5455` UTC Unix times override present fields |
| `type` | Infer from Unix mode when available; otherwise directory marker and symlink hints |
| `compression` | `compress_type` mapped to `CompressionMethod` |
| `is_encrypted` | `flag_bits & 0x1 != 0` |

Invalid DOS or NTFS timestamp values SHALL fall through to the next valid
precedence layer or `None` and emit `MEMBER_TIMESTAMP_INVALID`. With
`read_link_targets=True` (the default), if listing cannot read an encrypted
symlink target because no correct password is available, `link_target` SHALL
remain unset and `SYMLINK_TARGET_UNAVAILABLE` SHALL be emitted with reason
`"password_required"`. With `read_link_targets=False`, listing reads no symlink
target and emits nothing for it (`archive-reading`, "Link targets stored as member
data are read only when configured"). Diagnostic payloads SHALL
not include passwords, candidates, provider returns, key material, or decrypted
target bytes. Under `RAISE`, listing halts with `DiagnosticRaisedError`.

#### Scenario: ZIP metadata matrix

| Case | Expected |
| --- | --- |
| Unix entry with non-zero `external_attr` | `member.mode = external_attr >> 16` |
| Non-Unix entry or missing attrs | `member.mode is None` |
| Extended Timestamp carries modification time | `member.modified` is timezone-aware UTC from `0x5455`, overriding DOS / NTFS |
| NTFS FILETIMEs present, no Extended Timestamp | Present `modified` / `accessed` / `created` fields are timezone-aware UTC from `0x000A` |
| `flag_bits & 0x1` | `member.is_encrypted is True` |
| Out-of-range NTFS or DOS timestamp | Fallback value used; `MEMBER_TIMESTAMP_INVALID` counted and may attach to member |
| Timestamp diagnostic resolves to `RAISE` | Listing halts with `DiagnosticRaisedError` |
| Encrypted symlink target unavailable | Listing continues with `link_target=None`; `SYMLINK_TARGET_UNAVAILABLE` contains no secret |
| Encrypted symlink, `read_link_targets=False` | Listing reads no target data; `link_target=None`; no diagnostic |
