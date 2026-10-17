# Design: stored encrypted RAR members without a program

This change replaces the parked `rar5-stored-encrypted-native-read` (RAR5 only, parked
2026-10-07). Its RAR5 measurements still hold and are repeated here; its "RAR5 only"
section does not, because the RAR3/4 salt has been parsed since the password-list work
(`RarMemberInfo.rar3_salt`).

## The structure being exploited

A stored (`-m0`) member's packed bytes are its content once decrypted. File data
encryption is applied to the packed bytes of each member separately, so decryption is the
whole pipeline:

| Version | Cipher | Key and IV |
| --- | --- | --- |
| RAR 2.9-4 (extract version 29+) | AES-128-CBC | Both from the password and the member's 8-byte header salt: the SHA-1 loop of `2**18` rounds (`RarKdfCache.rar3`) |
| RAR5 | AES-256-CBC | Key = PBKDF2-HMAC-SHA256 at `1 << kdf_count` (`rar5_data_key`); IV from the member's encryption record |

The RAR5 key is not the HashKey (`+16` rounds) or the PswCheck (`+32`). Confusing them
gives plausible bytes that only a comparison against `unrar` rejects, which is why
`test_bytes_match_unrar` exists and needs `cryptography` (without it both sides would be
`unrar`).

The ciphertext is padded up to a whole 16-byte block and the true length is carried out
of band, the 7z AES shape `AesDecryptStream` was built for:

| `file_size` | `compress_size` (ciphertext) |
| --- | --- |
| 0 | 0 |
| 7 | 16 |
| 16 | 16 |
| 3000 | 3008 |
| 3001 | 3008 |

The read opens the ciphertext view over `compress_size`, wraps it in `AesDecryptStream`,
and cuts it to `file_size` with `SlicingStream`. Taking the length from the view would
hand the caller the padding. `_natively_decryptable` refuses sizes that do not fit this
shape, so a damaged header goes to the program instead of being decrypted wrongly.
Byte-for-byte equality with `unrar p` is tested for every size above, in RAR4 and RAR5.

## Decisions

### One part only

Each part of a member split across volumes carries its own encryption record (measured
on `rar` 7.00: a different IV per part, and the tweaked-checksum flag only on the last
part), and the part boundaries are not block-aligned. Decrypting the joined parts as one
CBC stream gives wrong bytes. Split encrypted members go to the program. Supporting them
would need the per-part records the parser does not keep.

Measuring this found a bug on main: the merged member kept the first part's record but
the last part's digest, so a tweaked CRC was published as a plaintext CRC32 and every
read failed its digest check, through `unrar` too. The merge now takes the last part's
record with its digest.

### Password choice follows the `unrar` read

A RAR5 PswCheck picks the password, and a wrong one raises `EncryptionError` before any
byte. With no usable check, a candidate list is judged by the CRC over a native
decryption (`_stored_check`, which now also covers RAR5 without a PswCheck, applying
`ConvertHashToMAC` under the candidate's HashKey when the CRC is tweaked). One RAR3/4
password goes unjudged, as it does to `unrar`: the digest at the end of the read is its
test, and an abandoned read emits `ENCRYPTED_MEMBER_UNVERIFIED`.

### No `cryptography`

An installation without `[recommended]` read these members through `unrar` before this
change, so the program still reads them there. When the program is missing too, the
`PackageNotInstalledError` names both, because either would serve the member (maintainer
decision on PR 356, 2026-09-18). Under `rar_decompressor="none"` there is no program, so
the read raises `PackageNotInstalledError` naming `cryptography`, where it used to raise
`UnsupportedFeatureError`. Nothing inside `src/` catches that refusal; the change in
exception type reaches only external callers, and the docs say so.

### The disk-copy note

The open-time note for a stream source said a *compressed* member triggers the
whole-archive copy. A stored encrypted member read by the program (no `cryptography`,
split, or RAR 2.0's cipher) triggers it too, so the notes and the spec now say "a member
the program has to read, such as a compressed one", which is true before and after.

### Left alone: the RAR 2.9-4 symlink target

`_ensure_link_target` reads a RAR 2.9-4 symlink's target out of its stored data at
listing time, and still refuses an encrypted one. Decrypting it there would set
`link_target`, stop a `SYMLINK_TARGET_UNAVAILABLE` diagnostic that a strict policy
refuses on, and split RAR from ZIP, which has the same guard. That is a change to public
behaviour, so it is put to the maintainer separately and kept open in the RAR handbook
(`dev-docs/formats/rar.md` §7); this change only corrects the comment and the diagnostic
text, which claimed the reader cannot decrypt the bytes.

### Rejected: decrypt natively, then hand the plaintext to `unrar`

This would seem to extend the approach to compressed encrypted members. It cannot work:
`unrar` takes an archive path and does its own header parsing and decryption, so there is
no way to feed it a decrypted member stream. A compressed encrypted member needs either
the program or a native RAR decompressor, which ADR 0002 rules out.

### The UnRAR licence

The licence forbids reimplementing RAR compression. A stored member has nothing to
decompress: this adds AES-CBC (`cryptography`) and the key derivations already in the
tree under `rarfile`'s ISC licence. ADR 0002 records the boundary as decompression, not
data.
