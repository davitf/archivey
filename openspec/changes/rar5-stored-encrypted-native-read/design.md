# Design — stored encrypted RAR5 without `unrar`

Measurements below were taken during the #342 review (2026-09-16, Claude Code container,
`cryptography` 49.0.0, RARLAB `rar`/`unrar` 7.0.7, Python 3.11) against `rar -m0 -p…`
archives.

## The structure being exploited

RAR5 member data encryption is AES-256-CBC over the **packed** bytes, keyed per member
from a FILE-header `CRYPT` extra record — algorithm, flags, `kdf_count`, a 16-byte salt, a
16-byte IV and an optional 12-byte PswCheck. `_parse_rar5_file_encryption` already parses
all of it into `RarMemberInfo.file_encryption`; nothing in the parser needs to change.

For a **stored** (`-m0`) member the packed bytes *are* the plaintext, so decryption is the
whole pipeline. The shape matches 7z AES exactly — CBC over a stream padded up to the
block boundary, with the true length carried out of band — which is why `AesDecryptStream`
(#342) fits with no new stream machinery:

| `file_size` | `compress_size` (ciphertext) | padding |
| --- | --- | --- |
| 7 | 16 | 9 |
| 3000 | 3008 | 8 |
| 3001 | 3008 | 7 |

`_direct_view` already takes an explicit `length`; the ciphertext view is opened over
`compress_size` and the decrypted stream is trimmed to `file_size`. Taking the length from
the view instead would hand the caller the CBC padding.

**Verified byte-for-byte during the #342 review**: key + IV + `AesDecryptStream` over the
direct slice reproduces `unrar p` output at 7, 3000 and 3001 bytes, and seeks correctly.
That is the premise of this change, not a hope about it.

## Decisions

### RAR5 only

`rar_parser.py` sets `file_encryption=None` unconditionally on the RAR3/RAR4 path
(`rar_parser.py:1395`). RAR4 keeps its 8-byte salt in the `LHD` header with a different
(SHA-1-based) key schedule, and none of it is parsed today. Widening the gate to "any
encrypted stored member" would therefore fault on a `None`; the gate must test for a
parsed `file_encryption`, which is the same thing as testing for RAR5. Parsing the RAR4
salt is a separate change with its own KDF work and its own fixtures.

### The key is `1 << kdf_count`, not the HashKey

`rar5_hash_key`'s docstring already records UnRAR's `crypt5.cpp` offset scheme: the AES
key is PBKDF2 at `1 << kdf_count`, the HashKey at `+16`, the PswCheck at `+32`. The
existing helper returns the **HashKey** (it exists for `ConvertHashToMAC`), so this change
adds a sibling that returns the data key — `_rar5_s2k(password, salt, 1 << kdf_count)` —
rather than reusing it. Getting these two confused produces plausible-looking garbage that
no test catches unless it compares against `unrar`, which is why task 3.1 does.

`_rar5_decrypt_header` already derives exactly this value for the header stream and caches
it on `_Rar5HdrEnc`. The file-data helper is per member (each member has its own salt), so
a cache would have to be keyed by `(password, salt, kdf_count)`; deriving once per opened
member is enough for now and is what the header path did before it grew a cache.

### PswCheck on a FILE record is assumed-compatible, not known-compatible

`_check_rar5_password` is written for the archive `ENCRYPTION` block. The FILE `CRYPT`
record's check value has the same declared 12-byte layout (8 PswCheck bytes + a 4-byte
SHA-256 checksum over them) and `_tweaked_hash_key` already calls it with a *file*
record's fields. That is evidence, not proof — a fixture pair (correct/wrong password)
must show it returning `True` and raising respectively before the direct path relies on
it (task 1.3). If it turns out not to hold, the fallback is to route wrong-password
detection through the existing digest check instead, which is strictly weaker but not
wrong.

### Wrong password must fail before plaintext, when it can

With a PswCheck present, `EncryptionError` is raised at open. Without one, a wrong key
produces garbage that only the tweaked CRC32/BLAKE2sp catches at the end of the read —
which is what happens today through `unrar` too, so this is not a regression. The
difference worth stating in the spec is that the direct path must not be *quieter* than
the spawn: `_wrap_payload_stream` already applies `_tweaked_verify_spec`, and the direct
branch at `rar_reader.py:1296` already goes through it, so verification comes for free.
The task list checks that rather than assuming it.

### No crypto backend → fall back, do not fail

An installation without `[recommended]` reads these members through `unrar` today. Making
the new path mandatory would turn a working archive into a `PackageNotInstalledError` for
anyone who has the binary but not the library. The gate therefore includes backend
availability, and only the case where *neither* route exists raises — naming both, since
either one alone would have served the member.

### Rejected: decrypt natively, then hand the plaintext to `unrar`

This would generalise to compressed encrypted members, and it cannot work: `unrar` takes
an *archive path* and does its own header parsing and decryption. There is no way to feed
it a decrypted member stream. Encrypted compressed members are all-or-nothing, and
"all" means a native RAR decompressor, which ADR 0002 rules out.

### Rejected: widening `_ensure_link_target` in the same change

`_ensure_link_target` (`rar_reader.py:1206`) carries its own `not raw.is_encrypted` guard
(`:1217`) on the RAR4 symlink-target-as-data path. RAR5 symlinks carry their target in `file_redir`
and never reach it, so this change does not touch it. It is listed here so the next reader
does not treat the leftover guard as an oversight.

### The UnRAR licence question

The UnRAR licence forbids reimplementing the RAR *compression* algorithm. A `-m0` member
has nothing to decompress: what is added here is AES-CBC plus PBKDF2-HMAC-SHA256, both
stdlib/`cryptography`, and the RAR5 KDF wiring already in the tree under `rarfile`'s ISC
licence. No decompression algorithm is derived or reimplemented, and every compressed
member still goes to the RARLAB binary.

### The temp-copy caveat says "compressed" where it means "needs the spawn"

`format-rar`'s "Serve random access and extraction with bounded explicit temp use" warns a
non-path stream caller that reading a **compressed** member copies the whole archive to
disk. That has always been understated: a stored *encrypted* member goes through the spawn
and triggers the copy too. `rar5-stored-encrypted-native-read` removes exactly the RAR5
half of that set, which is what makes the wording this change's problem rather than a
pre-existing one to leave alone.

Rejected: leaving the word "compressed" and noting the requirement was considered. It would
stay understated for RAR4 stored encrypted members, which keep spawning — so the caveat
would be wrong in a way this change is directly responsible for narrowing. Widening it to
"a member that requires the RARLAB spawn" makes it true before and after, and the RAR4
scenario row is what keeps it honest.

## ADR amendment

ADR [0002](../../../dev-docs/decisions/0002-native-rar-metadata-unrar-data.md) says
"decompress member **data** via the RARLAB `unrar` binary" and scopes native decryption to
*headers*. Its "Decision" and "Consequences" sections must be amended to record that
stored members — including encrypted RAR5 ones — are served natively, and that the
boundary is *decompression*, not *data*. That edit is part of this change (task 4.1); the
existing wording is the thing a future reader would cite to undo it.
