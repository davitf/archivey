# format-7z — bounded confirmation delta

> Replaces the wrong-password-detection half of the existing encryption requirement. The
> KDF, `NumCyclesPower` clamp, header-encryption and `is_encrypted` rules are unchanged
> and pasted verbatim (a MODIFIED requirement replaces the whole block).

## MODIFIED Requirements

### Requirement: Decrypt AES-encrypted 7z with archive-reading passwords

The system SHALL read AES-256-encrypted 7z folders and header-encrypted archives
when a valid password and crypto backend are available. Header encryption SHALL
decrypt the end header before listing; without a password the system raises
`EncryptionError`, and with no crypto backend it raises `PackageNotInstalledError`.
Any archive or folder encryption SHALL set `ArchiveInfo.is_encrypted` to `True`.

Passwords SHALL use the `archive-reading` candidate model: known-good successes
for this reader, remaining static candidates, then provider requests. Header
requests use `member is None`; folder/member requests identify the member being
decrypted where possible. Members in different encrypted folders MAY use different
passwords in one open or one `stream_members()` pass. Key derivation SHALL use the
7z SHA-256 scheme (UTF-16LE password, salt, `1 << NumCyclesPower` rounds with the
documented `0x3F` no-hash sentinel) via a 7z-local helper that feeds `AesParams` into
the shared crypto stage — not a generic crypto-surface KDF. `NumCyclesPower` values
other than `0x3F` SHALL be accepted only when `≤ 24`; values 25–62 SHALL raise
`UnsupportedFeatureError` (matching 7-Zip’s decoder clamp), not
`EncryptionError`.

Because 7z AES carries no password check value in the format, wrong-password detection
relies on a derived cheap key check, integrity anchors, and codec rejection, applied as
the `archive-reading` confirmation ladder. The reader SHALL cache derived keys by
`(password, salt, cycles)` and try known-good passwords first.

**Cheap key check (tail padding).** The AES coder's packed stream is CBC over a plaintext
padded to a 16-byte boundary; the coder's declared output length gives the unpadded
length, so `pack_size - unpack_size` bytes at the tail are padding. Where that padding is
**≥ 4 bytes**, the reader SHALL decrypt the final CBC block — reachable from the last two
ciphertext blocks alone, without decoding the folder — and treat an all-zero padding tail
as `CONFIRMED`. A non-zero tail SHALL NOT reject the candidate: the padding value is a
writer convention, not a format guarantee. Where the padding is **< 4 bytes** the check
SHALL be skipped entirely, in either direction — under 32 bits it can neither confirm nor
safely eliminate, and a writer that pads with residue would otherwise cause the correct
password to be dropped.

**Integrity anchor.** Confirmation SHALL stop at the earliest sufficient anchor rather
than decoding the folder: per-member CRCs are consulted in substream order, and the plan
terminates once CRC-verified bytes reach 4. A folder digest SHALL be used only when it is
the earliest such anchor — a folder carrying both a digest and per-member CRCs SHALL
anchor on the members.

**Codec rejection.** With no anchor in budget, a folder whose chain contains a codec that
rejects a wrong key (every LZMA-family, BZip2 and Deflate chain 7z uses) SHALL decode a
bounded plaintext prefix and treat a survivor as `INCONCLUSIVE`. A `Copy` or PPMd chain
has no such filter and follows the `archive-reading` rule for that case: the unbounded
pass only when the candidate set is ambiguous.

When an encrypted folder has **no** folder digest and a member has **no** CRC
(format-legal for store/copy), the system SHALL still return decoded bytes (best-effort,
matching 7-Zip) and SHALL emit `DIGEST_UNVERIFIABLE` with
`DigestContext.reason="no_integrity_anchor"` — it MUST NOT imply the decryption was
authenticated, and a tail-padding confirmation SHALL NOT suppress it, since that check
attests the key and not the data. The reader SHALL NOT decode such a folder merely to
discover that nothing can be checked. After decoding a header-encrypted
`kEncodedHeader`, a parsed result with zero file records SHALL raise `EncryptionError`
(legitimate writers never encrypt an empty header) so a wrong password cannot open as a
silent empty listing.

#### Scenario: encryption matrix

| Case | Expected |
| --- | --- |
| Header-encrypted archive, no password/provider result | `EncryptionError` before listing |
| Header-encrypted archive, valid password + crypto | Header is decrypted natively; members list; `is_encrypted` true |
| Encrypted folders with different passwords | Each folder uses its matching candidate in random access or one streaming pass |
| Sole wrong password, integrity anchor present (folder digest and/or member CRC) or compressed codec rejects garbage | `EncryptionError`/`CorruptionError`; no incorrect data handed to the caller as success |
| Header-encrypted archive, wrong password decodes to zero file records | `EncryptionError` (never a silent empty listing) |
| Encrypted store/copy member, no folder digest, no member CRC, any password | Bytes returned; `DIGEST_UNVERIFIABLE` (`reason="no_integrity_anchor"`) emitted |
| `NumCyclesPower` 25–62 | `UnsupportedFeatureError` (not remapped to wrong-password) |
| Repeated salt/cycles/password | Derived key cache avoids repeated key derivation |
| Sole wrong password, no anchor within budget, compressed folder | `EncryptionError` at open (the codec rejects within the prefix) |
| Sole wrong password, no anchor within budget, store/copy folder | Accepted at open; surfaces on the caller's read; `ENCRYPTED_MEMBER_UNVERIFIED` on an abandoned partial read |
| Ambiguous candidates, store/copy folder, only anchor at folder end | Unbounded pass; the candidate matching the CRC wins |
| Solid folder, first member 4 KiB, folder 200 MiB | Confirmation decodes the first member only |
| Folder carrying both a folder digest and per-member CRCs | Anchors on the earliest member CRC, not the folder digest |
| AES tail padding ≥ 4 bytes, all zero for exactly one candidate | `CONFIRMED` with no folder decode |
| AES tail padding ≥ 4 bytes, non-zero for every candidate | No candidate dropped; ladder continues normally |
| AES tail padding < 4 bytes | Check skipped; ladder starts at the integrity anchor |
