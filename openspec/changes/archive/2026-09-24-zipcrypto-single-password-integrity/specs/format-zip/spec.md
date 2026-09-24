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
candidate failure (defined below) on the caller's `read` or forward `seek` SHALL raise
`EncryptionError` explaining that the password may be wrong or the member may be
corrupt, not `CorruptionError`. It SHALL NOT be marked as a wrong-password verdict:
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
| One distinct static candidate that passes the verification byte but is wrong, or right on a corrupt member | Caller `read`/`seek` raises `EncryptionError` saying the password may be wrong or the member corrupt; no wrong-password mark |
| Large compressed member | At most bounded prefix decompressed per candidate; no proportional plaintext storage; caller stream still checks CRC at EOF |
| STORED member with several surviving candidates | One shared ciphertext pass computes every candidate CRC; matching candidate accepted and reopened |
| Multiple STORED CRC matches | Earliest matching candidate in order wins |
| Corruption beyond confirmed prefix | Caller read raises `CorruptionError` where the ordinary ZIP path detects it |
| Candidates fail confirmation | `EncryptionError` says password may be wrong or member corrupt; no bytes returned |
| Non-BZIP2 `OSError("Invalid data stream")` or any unrelated `OSError` | Propagates unchanged; failed stream is closed |
| Structural `BadZipFile` | `CorruptionError`; no further password iteration |
