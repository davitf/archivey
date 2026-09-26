# format-zip — damaged WinZip AES members delta

## MODIFIED Requirements

### Requirement: Read WinZip AES-encrypted members

The ZIP backend SHALL read WinZip AES (AE-x) encrypted members: compression
method 99 with the AES extra field `0x9901` giving vendor version (AE-1/AE-2),
key strength (128/192/256), and the actual underlying compression method.
Decryption SHALL derive keys via PBKDF2-HMAC-SHA1 (1000 iterations) over the
password and per-member salt (strength/16 bytes) into encryption key ‖
authentication key ‖ 2-byte verification value, decrypt with AES-CTR
(little-endian counter), and authenticate the ciphertext with HMAC-SHA1
truncated to 10 bytes. Decrypted bytes SHALL be decompressed through the shared
codec layer for the actual method.

A wrong password SHALL fail fast on the 2-byte verification value with
`EncryptionError` (no bytes returned). A ciphertext HMAC mismatch SHALL raise
at the terminal read (`CorruptionError`), whether one password or several were
tried: a wrong password passes the verification value once in 65 536, so a member
that every candidate passing it fails is reported as damaged. AE-2 members SHALL surface no
`crc32` (the ZIP CRC is 0; integrity is the HMAC) and run no CRC check; AE-1
members SHALL surface and verify `crc32` in addition to the HMAC. AES
decryption requires `cryptography` (`[recommended]`); when it is absent an AE member SHALL raise
`PackageNotInstalledError` (detection still identifies the member as
AES-encrypted). With several possible passwords, WinZip AES candidates SHALL be
confirmed as "Confirm multi-candidate ZipCrypto passwords" describes, not accepted on
the verification value alone.

#### Scenario: WinZip AES matrix

| Case | Expected |
| --- | --- |
| AE-1 or AE-2 member, 128/192/256, correct password, `cryptography` present | Decrypts, decompresses via codec layer, HMAC verified at EOF |
| Wrong password | `EncryptionError` on the 2-byte verification value; no bytes |
| Tampered ciphertext, correct password | HMAC mismatch → `CorruptionError` at terminal read |
| Tampered ciphertext, several candidates including the correct one | `CorruptionError` naming the member as most likely corrupt |
| Tampered ciphertext, partial read then `close()` | Quiet; `close()` is teardown, not a verdict (ADR 0014) |
| AE-2 member | `crc32` absent; no CRC check; HMAC is the integrity signal |
| AE-1 member | `crc32` present and verified alongside the HMAC |
| AES member without `cryptography` installed | `PackageNotInstalledError`; still reported as encrypted |
| Several candidates, a wrong one passing the verification value first | Wrong candidate rejected by the confirm; the right one reads |

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
the member may be corrupt, not `CorruptionError`; for an `LZMA` or `PPMd` member, whose
codec header is read when the member opens, the open raises it. It SHALL NOT be marked
as a wrong-password verdict: nothing in the archive tells a colliding wrong password
from a damaged member. A seek off the read frontier forfeits the CRC (`compressed-streams`,
ADR 0014), so a STORED member's seek does not raise; closing that stream emits
`ENCRYPTED_MEMBER_UNVERIFIED`.

ZIP's two per-open checks are the `archive-reading` ladder's **cheap key check** rung:
ZipCrypto's one header verification byte (2⁻⁸) and WinZip AES's two-byte `pw_verify`
(2⁻¹⁶). Neither reaches 2⁻³², so neither SHALL confirm on its own; both eliminate. Neither
cipher pads — ZipCrypto is a byte-wise stream cipher and WinZip AES is CTR, so ciphertext
length equals plaintext length in both — so ZIP has no tail-padding check to add.

A member stream whose password only one of those checks accepted, closed before EOF,
SHALL emit `ENCRYPTED_MEMBER_UNVERIFIED` (`check="weak_open_check"`): a wrong ZipCrypto
password that passes the byte check returns readable garbage from a partial read and
fails only at the CRC, and a WinZip AES member's HMAC runs only at EOF.

Compressed members SHALL confirm by decompressing a bounded plaintext prefix and
discarding it. If EOF is reached within the bound, the CRC check makes confirmation
exact. Confirmation SHALL run through `plan_password_confirm` /
`run_password_confirm_plan` as the probe under `_PasswordCandidates.attempt`, with the
member as the one substream. Only `DEFLATE`, `BZIP2` and `LZMA` count as rejecting
codecs; they are stream codecs, or bzip2, which produces output after one block, so the
compressed input a bounded prefix consumes is bounded by the format and needs no
separate cap. Any other method walks to the CRC. A compressed member larger than the
prefix yields an `INCONCLUSIVE` survivor: its stream, closed before EOF, SHALL emit
`ENCRYPTED_MEMBER_UNVERIFIED` (`check="confirm_budget_exhausted"`). The same probe
confirms WinZip AES candidates. Where an AES member's CRC is out of reach (AE-2 stores
none) and the member fits the prefix or its codec does not reject, the probe SHALL read
the member to its end, where the HMAC, which covers the whole ciphertext, confirms or
rejects the candidate. STORED members
SHALL disambiguate all surviving candidates in one shared ciphertext pass, computing each
candidate's plaintext CRC-32 in constant memory; if multiple candidates match, candidate
order wins. No candidate plaintext may be buffered. The STORED shared pass stays
ZIP-local, since running every candidate over one pass is a shape the per-candidate probe
does not model.

After confirmation, the reader SHALL open a fresh caller stream with the accepted
password, promote it to known-good when confirmation reached the member's CRC, and retain
ordinary read-time integrity checking. An `INCONCLUSIVE` survivor is not promoted: this
path runs only for an ambiguous candidate set. Confirmation failure for all candidates
SHALL raise `EncryptionError` explaining that passwords may be wrong or the member may be
corrupt. For WinZip AES, where each failing candidate had passed the 16-bit `pw_verify`,
it SHALL raise `CorruptionError` instead, as "Read WinZip AES-encrypted members" says.

Candidate failures SHALL be the `CorruptionError` a wrong key's garbage produces (a
codec rejection, a CRC or HMAC mismatch), and a codec's `TruncatedError` while the
member's whole payload is in the file. Local-header mismatch, bad local-header magic,
overlap and other structural failures raise before decryption starts and SHALL become
`CorruptionError` immediately, with no further password iteration; a payload the file
cuts short stays `TruncatedError`. `UnsupportedFeatureError`,
`PackageNotInstalledError`, `ResourceLimitError` and `OSError` values SHALL propagate
unchanged. Rejected-candidate streams SHALL be closed before trying the next candidate.

#### Scenario: ZipCrypto confirmation matrix

| Case | Expected |
| --- | --- |
| Wrong candidate passes verification byte before correct one (STORED / DEFLATE / BZIP2 / LZMA) | Wrong candidate rejected; fresh stream opened with correct candidate |
| One distinct static candidate | No confirmation read; member streams lazily |
| One distinct static candidate that passes the verification byte but is wrong, or right on a corrupt member | Caller `read`/`readinto`/forward `seek` of a compressed member raises `EncryptionError` saying the password may be wrong or the member corrupt (at open for `LZMA`/`PPMd`); no wrong-password mark |
| Same, STORED member, caller seeks and closes | Seek does not raise; `ENCRYPTED_MEMBER_UNVERIFIED` on close |
| Large compressed member | At most bounded prefix decompressed per candidate; no proportional plaintext storage; caller stream still checks CRC at EOF |
| STORED member with several surviving candidates | One shared ciphertext pass computes every candidate CRC; matching candidate accepted and reopened |
| Multiple STORED CRC matches | Earliest matching candidate in order wins |
| Corruption beyond confirmed prefix | Caller read raises what the codec layer raises for the same damage unencrypted (`CorruptionError` or `TruncatedError`) |
| ZipCrypto candidates fail confirmation | `EncryptionError` says password may be wrong or member corrupt; no bytes returned |
| WinZip AES candidates all fail after passing `pw_verify` | `CorruptionError`; no bytes returned |
| `OSError` from the source | Propagates unchanged; failed stream is closed |
| Structural local-header damage | `CorruptionError`; no further password iteration |
| One distinct static candidate, stream closed before EOF | Data returned; `ENCRYPTED_MEMBER_UNVERIFIED` (`check="weak_open_check"`) |
| Several candidates, STORED or compressed member within the prefix, stream closed before EOF | No diagnostic (the CRC confirmed the winner) |
| Several candidates, compressed member past the prefix, stream closed before EOF | `ENCRYPTED_MEMBER_UNVERIFIED` (`check="confirm_budget_exhausted"`); winner not added to known-good |
| WinZip AES member, wrong password | Rejected by `pw_verify`; HMAC remains the authoritative check |
| WinZip AES member, stream closed before EOF | `ENCRYPTED_MEMBER_UNVERIFIED` (`check="weak_open_check"`) |
