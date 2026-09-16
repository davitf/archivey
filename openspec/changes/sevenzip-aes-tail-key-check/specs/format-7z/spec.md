# format-7z — AES tail key check delta

> Pastes the post-`bounded-password-confirmation` encryption requirement, so archive that
> change first. This delta adds only the cheap-key-check rung and its scenario rows.

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
padded to a 16-byte boundary; the coder's declared output length gives the unpadded length,
so `pack_size - unpack_size` tail bytes are padding. Where that padding is **≥ 4 bytes**,
the reader SHALL decrypt the final CBC block — reachable from the last two ciphertext
blocks alone, without decoding the folder — and treat an all-zero padding tail as
`CONFIRMED`. A non-zero tail SHALL NOT reject the candidate: the padding value is a writer
convention, not a format guarantee. Where the padding is **< 4 bytes** the check SHALL be
skipped entirely, in either direction — under 32 bits it can neither confirm nor safely
eliminate, and a writer that pads with residue would otherwise cause the correct password
to be dropped.

**Integrity anchor.** Confirmation SHALL stop at the earliest sufficient anchor rather
than decoding the folder: per-member CRCs are consulted in substream order, and the plan
terminates once CRC-verified bytes reach 4. A folder digest SHALL be used only when it is
the earliest such anchor — a folder carrying both a digest and per-member CRCs SHALL
anchor on the members. If that anchor sits past `CONFIRM_PREFIX_BYTES` and the chain
has a rejecting codec, the plan SHALL NOT walk it (codec rejection settles a wrong
key). If the chain has no rejecting codec, the plan SHALL walk it anyway.

**Codec rejection.** A chain rejects iff it contains a decompressor measured to fail on
random AES output. Measured: LZMA1, LZMA2, BZip2, Deflate. Filters (Delta, BCJ) never
reject — `MethodKind.LZMA_FAMILY` includes Delta and is the wrong predicate. PPMd,
Deflate64, ZSTD, Brotli and LZ4 are treated as non-rejecting until measured. A
rejecting chain with no reachable anchor decodes a bounded plaintext prefix and treats
a survivor as `INCONCLUSIVE`. A non-rejecting chain follows the `archive-reading`
rule: walk a late CRC; with no CRC at all, do not invent an unbounded decode.

When an encrypted folder has **no** folder digest and a member has **no** CRC
(format-legal for store/copy), the system SHALL still return decoded bytes (best-effort,
matching 7-Zip) and SHALL emit `DIGEST_UNVERIFIABLE` with
`DigestContext.reason="no_integrity_anchor"` — it MUST NOT imply the decryption was
authenticated — a tail-padding confirmation SHALL NOT suppress it, since that check
attests the key and not the data. The reader SHALL NOT decode such a folder merely to
discover that nothing can be checked — several candidates do not change that. After decoding a header-encrypted
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
| Sole wrong password, LZMA2, CRC at 200 MiB | `EncryptionError` at open (codec rejects in the prefix; no 200 MiB read) |
| Correct password, LZMA2, CRC at 200 MiB, `read(1)` then close | `INCONCLUSIVE`; `ENCRYPTED_MEMBER_UNVERIFIED` |
| Sole wrong password, store/copy, CRC at 200 MiB | Walk to the CRC; `EncryptionError` at open |
| Correct password, store/copy, CRC at 200 MiB, `read(1)` then close | No diagnostic (anchor confirmed) |
| Sole wrong password, store/copy, no CRC at all | Accepted at open; surfaces on the caller's read; `ENCRYPTED_MEMBER_UNVERIFIED` on an abandoned partial read |
| Store/copy, no CRC, two candidates | First candidate; `DIGEST_UNVERIFIABLE`; confirmation ≤ budget |
| Ambiguous candidates, store/copy folder, only CRC at folder end | Unbounded pass; the candidate matching the CRC wins |
| AES → Delta → Copy or AES → BCJ → Copy | Treated as non-rejecting (the filter does not reject random input) |
| Solid folder, first member 4 KiB, folder 200 MiB | Confirmation decodes the first member only |
| Folder carrying both a folder digest and per-member CRCs | Anchors on the earliest member CRC, not the folder digest |
| AES tail padding ≥ 4 bytes, all zero for exactly one candidate | `CONFIRMED` with no folder decode |
| AES tail padding ≥ 4 bytes, non-zero for every candidate | No candidate dropped; ladder continues normally |
| AES tail padding < 4 bytes | Check skipped; ladder starts at the integrity anchor |
| No integrity anchor, several candidates, tail padding ≥ 4 bytes | The matching candidate is chosen; `DIGEST_UNVERIFIABLE` still emitted |
