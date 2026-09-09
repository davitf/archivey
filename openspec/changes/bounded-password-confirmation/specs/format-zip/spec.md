# format-zip — shared confirmation driver delta

> Names ZIP's existing per-open checks as the ladder's cheap-key-check rung, routes the
> compressed path through the shared driver, and adds the partial-read diagnostic. No
> change to what ZIP accepts or rejects.

## MODIFIED Requirements

### Requirement: Confirm multi-candidate ZipCrypto passwords

For traditional ZipCrypto, the per-open verification byte is weak and the
authoritative check is CRC-32 plus decompressor completion. When another
distinct candidate may be tried, the ZIP reader SHALL confirm a candidate that
passes the byte check before accepting it, following `archive-reading` weak-check
confirmation and bounded-storage rules. With one distinct static candidate
(duplicates included), the reader SHALL keep the normal lazy stream path; any
read-time integrity failure is translated normally.

ZIP's two per-open checks are the `archive-reading` ladder's **cheap key check** rung:
ZipCrypto's one header verification byte (2⁻⁸) and WinZip AES's two-byte `pw_verify`
(2⁻¹⁶). Neither reaches 2⁻³², so neither SHALL confirm on its own; both eliminate. Neither
cipher pads — ZipCrypto is a byte-wise stream cipher and WinZip AES is CTR, so ciphertext
length equals plaintext length in both — so ZIP has no tail-padding check to add.

Compressed-member confirmation SHALL run through the shared confirmation driver in
`internal/password_confirm.py`, returning the three-valued verdict; the STORED shared
ciphertext pass stays ZIP-local, since running every candidate over one pass is a shape
the per-candidate driver does not model.

A candidate accepted on the verification byte alone, whose member stream is then closed
before EOF, SHALL emit `ENCRYPTED_MEMBER_UNVERIFIED` — a wrong ZipCrypto password that
passes the byte check returns readable garbage from a partial read and fails only at the
CRC.

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
| Large compressed member | At most bounded prefix decompressed per candidate; no proportional plaintext storage; caller stream still checks CRC at EOF |
| STORED member with several surviving candidates | One shared ciphertext pass computes every candidate CRC; matching candidate accepted and reopened |
| Multiple STORED CRC matches | Earliest matching candidate in order wins |
| Corruption beyond confirmed prefix | Caller read raises `CorruptionError` where the ordinary ZIP path detects it |
| Candidates fail confirmation | `EncryptionError` says password may be wrong or member corrupt; no bytes returned |
| Non-BZIP2 `OSError("Invalid data stream")` or any unrelated `OSError` | Propagates unchanged; failed stream is closed |
| Structural `BadZipFile` | `CorruptionError`; no further password iteration |
| One distinct static candidate, wrong password passes the byte check, stream closed before EOF | Data returned; `ENCRYPTED_MEMBER_UNVERIFIED` (`check="weak_open_check"`) |
| WinZip AES member, wrong password | Rejected by `pw_verify`; HMAC remains the authoritative check |
