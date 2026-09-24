# format-zip — single ZipCrypto password integrity delta

## MODIFIED Requirements

### Requirement: Confirm multi-candidate ZipCrypto passwords

For traditional ZipCrypto, the per-open verification byte is weak and the
authoritative check is CRC-32 plus decompressor completion. When another
distinct candidate may be tried, the ZIP reader SHALL confirm a candidate that
passes the byte check before accepting it, following `archive-reading` weak-check
confirmation and bounded-storage rules. With one distinct static candidate
(duplicates included), the reader SHALL keep the normal lazy stream path, with no
confirmation read. Because only the verification byte vouched for that password, a
candidate failure (defined below) on the caller's `read`, `readinto` or forward
`seek` SHALL raise `EncryptionError` explaining that the password may be wrong or
the member may be corrupt, not `CorruptionError`. It SHALL NOT be marked as a wrong-password verdict:
nothing in the archive tells a colliding wrong password from a damaged member.

Compressed members (`DEFLATE`, `BZIP2`, `LZMA`) SHALL confirm by decompressing a
bounded plaintext prefix and discarding it. If EOF is reached within the bound,
the CRC check makes confirmation exact. STORED members SHALL disambiguate all
surviving candidates in one shared ciphertext pass, computing each candidate's
plaintext CRC-32 in constant memory; if multiple candidates match, candidate
order wins. No candidate plaintext may be buffered.

After confirmation, the reader SHALL open a fresh caller stream with the accepted
password, promote it to known-good, and retain ordinary read-time integrity
checking. Confirmation failure for all candidates SHALL raise `EncryptionError`
explaining that passwords may be wrong or the member may be corrupt.

Candidate failures SHALL include only `zipfile.BadZipFile` with `"Bad CRC-32 for
file ..."`, `zlib.error`, `lzma.LZMAError`, and exactly BZIP2's
`OSError("Invalid data stream")`. Local-header mismatch, bad local-header magic,
overlap, and other structural `BadZipFile` failures SHALL become
`CorruptionError` immediately. Other `OSError` values SHALL propagate unchanged.
Rejected-candidate streams SHALL be closed before trying the next candidate.

#### Scenario: ZipCrypto confirmation matrix

| Case | Expected |
| --- | --- |
| Wrong candidate passes verification byte before correct one (STORED / DEFLATE / BZIP2 / LZMA) | Wrong candidate rejected; fresh stream opened with correct candidate |
| One distinct static candidate | No confirmation read; member streams lazily |
| One distinct static candidate that passes the verification byte but is wrong, or right on a corrupt member | Caller `read`/`readinto`/`seek` raises `EncryptionError` saying the password may be wrong or the member corrupt; no wrong-password mark |
| Large compressed member | At most bounded prefix decompressed per candidate; no proportional plaintext storage; caller stream still checks CRC at EOF |
| STORED member with several surviving candidates | One shared ciphertext pass computes every candidate CRC; matching candidate accepted and reopened |
| Multiple STORED CRC matches | Earliest matching candidate in order wins |
| Corruption beyond confirmed prefix | Caller read raises `CorruptionError` where the ordinary ZIP path detects it |
| Candidates fail confirmation | `EncryptionError` says password may be wrong or member corrupt; no bytes returned |
| Non-BZIP2 `OSError("Invalid data stream")` or any unrelated `OSError` | Propagates unchanged; failed stream is closed |
| Structural `BadZipFile` | `CorruptionError`; no further password iteration |

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
`"password_required"`. When a ZipCrypto target's data fails its integrity check
under a password only the verification byte vouched for (the ambiguous
`EncryptionError` of "Confirm multi-candidate ZipCrypto passwords"), the reason
SHALL be `"password_or_damage"` and the message SHALL name both causes. With
`read_link_targets=False`, listing reads no symlink target and emits nothing for it
(`archive-reading`, "Link targets stored as member data are read only when
configured"). Diagnostic payloads SHALL not include passwords, candidates,
provider returns, key material, or decrypted target bytes. Under `RAISE`, listing
halts with `DiagnosticRaisedError`.

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
| ZipCrypto symlink target fails its integrity check under an unconfirmed password | Listing continues with `link_target=None`; `SYMLINK_TARGET_UNAVAILABLE` with reason `"password_or_damage"` |
| Encrypted symlink, `read_link_targets=False` | Listing reads no target data; `link_target=None`; no diagnostic |
