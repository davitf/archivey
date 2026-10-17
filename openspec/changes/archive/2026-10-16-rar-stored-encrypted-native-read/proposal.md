# Read stored, encrypted RAR members without a program

## Why

A stored (`-m0`) member's packed bytes are its content once decrypted, and every piece
needed to decrypt them was already in the tree: the RAR3/4 salt and SHA-1 key schedule
(the password-list check decrypts stored RAR3/4 members to test their CRC), the RAR5
encryption record and PBKDF2 key (the header path), and `AesDecryptStream`. Only the
member read still went to `unrar`. The maintainer asked for these members to be read
natively for RAR 2.9-4 and RAR5 (2026-10-16), which picks up this change after it was
parked on 2026-10-07 as RAR5 only.

## What Changes

- A stored, encrypted member in one part is decrypted by archivey under every
  `rar_decompressor` value, `none` included, when `cryptography` is installed. The read
  is trimmed to the unpacked size, digest-checked (RAR5 tweaked digests included) and
  seekable.
- The password is picked as for an `unrar` read: RAR5 PswCheck, RAR3/4 candidate lists
  judged by the CRC over a native decryption (now RAR5 without a PswCheck too), one
  RAR3/4 password unjudged with `ENCRYPTED_MEMBER_UNVERIFIED` on an abandoned read.
- Without `cryptography` the program reads the member; with neither, the
  `PackageNotInstalledError` names both. Under `none` it names `cryptography`.
- A split encrypted member, a compressed one, and RAR 2.0's cipher still need a program.
- Fixed on the way: a split RAR5 encrypted member failed its digest check because the
  merged member kept the first part's encryption record with the last part's digest.

## Impact

- Capabilities: `format-rar`, `packaging-and-extras`.
- Code: `internal/backends/rar_reader.py`, `rar_parser.py` (`rar5_data_key`, split merge).
- ADR 0002: the boundary is decompression, not data.
