## 1. Password lists that survive a bounded probe

- [x] 1.1 `_PasswordCandidates.attempt` gathers survivors (`settled` / `settle`); `attempt_with_confirm` in `password_confirm`
- [x] 1.2 ZIP and 7z supply their full check (walk to the CRC, or the WinZip AES HMAC)
- [x] 1.3 RAR: bounded probe and full check through the decompressor for members with no password check; native CRC check for stored RAR 2.9+ members
- [x] 1.4 `scripts/gen_rar_fixtures.py`: Ubuntu rar 6.23 fallback, `--only`; three encrypted RAR4 fixtures past the prefix
- [x] 1.5 Tests and docs
- [x] 1.6 `openspec archive password-list-confirm --yes`
