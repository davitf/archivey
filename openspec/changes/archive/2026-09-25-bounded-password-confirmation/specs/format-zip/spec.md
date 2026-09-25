# format-zip — shared confirmation driver delta

> Names ZIP's existing per-open checks as the ladder's cheap-key-check rung, routes the
> compressed path through the shared driver, and adds the partial-read diagnostic. No
> change to what ZIP accepts or rejects. Rebased on the current requirement text, which
> gained the lone-candidate `EncryptionError` rule after this delta was written.

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

ZIP's two per-open checks are the `archive-reading` ladder's **cheap key check** rung:
ZipCrypto's one header verification byte (2⁻⁸) and WinZip AES's two-byte `pw_verify`
(2⁻¹⁶). Neither reaches 2⁻³², so neither SHALL confirm on its own; both eliminate. Neither
cipher pads — ZipCrypto is a byte-wise stream cipher and WinZip AES is CTR, so ciphertext
length equals plaintext length in both — so ZIP has no tail-padding check to add.

A member stream whose password only one of those checks accepted, closed before EOF,
SHALL emit `ENCRYPTED_MEMBER_UNVERIFIED` (`check="weak_open_check"`): a wrong ZipCrypto
password that passes the byte check returns readable garbage from a partial read and
fails only at the CRC, and a WinZip AES member's HMAC runs only at EOF.

Compressed members (`DEFLATE`, `BZIP2`, `LZMA`) SHALL confirm by decompressing a
bounded plaintext prefix and discarding it. If EOF is reached within the bound,
the CRC check makes confirmation exact. Compressed-member confirmation SHALL run
through `plan_confirm` / `run_confirm_plan` as the probe under
`_PasswordCandidates.attempt`, with the member as the one substream; the candidate-failure
exception filter below SHALL survive the move. These three methods are stream codecs, or
bzip2, which produces output after one block, so the compressed input a bounded prefix
consumes is bounded by the format and needs no separate cap. A compressed member larger
than the prefix yields an `INCONCLUSIVE` survivor: its stream, closed before EOF, SHALL
emit `ENCRYPTED_MEMBER_UNVERIFIED` (`check="confirm_budget_exhausted"`). STORED members SHALL disambiguate all
surviving candidates in one shared ciphertext pass, computing each candidate's
plaintext CRC-32 in constant memory; if multiple candidates match, candidate
order wins. No candidate plaintext may be buffered. The STORED shared pass stays
ZIP-local, since running every candidate over one pass is a shape the per-candidate probe
does not model.

After confirmation, the reader SHALL open a fresh caller stream with the accepted
password, promote it to known-good when confirmation reached the member's CRC, and
retain ordinary read-time integrity checking. An `INCONCLUSIVE` survivor is not
promoted: this path runs only for an ambiguous candidate set. Confirmation failure for all candidates SHALL raise `EncryptionError`
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
| One distinct static candidate, stream closed before EOF | Data returned; `ENCRYPTED_MEMBER_UNVERIFIED` (`check="weak_open_check"`) |
| Several candidates, STORED or compressed member within the prefix, stream closed before EOF | No diagnostic (the CRC confirmed the winner) |
| Several candidates, compressed member past the prefix, stream closed before EOF | `ENCRYPTED_MEMBER_UNVERIFIED` (`check="confirm_budget_exhausted"`); winner not added to known-good |
| WinZip AES member, wrong password | Rejected by `pw_verify`; HMAC remains the authoritative check |
| WinZip AES member, stream closed before EOF | `ENCRYPTED_MEMBER_UNVERIFIED` (`check="weak_open_check"`) |
