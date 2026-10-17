## 1. Listing

- [x] 1.1 Decrypt an encrypted, stored RAR 2.9-4 symlink target in `_ensure_link_target` through the member read's decryption, with the CRC checked before the target is used
- [x] 1.2 Report a CRC failure as `password_or_damage`, no password as `password_required`, and an undecryptable target as `target_data_encrypted`

## 2. Tests and docs

- [x] 2.1 Fixture `symlinks_encrypted__rar4.rar` and tests for right, listed, missing and wrong passwords, no `cryptography`, and extraction
- [x] 2.2 RAR handbook reason table and §7 entry
