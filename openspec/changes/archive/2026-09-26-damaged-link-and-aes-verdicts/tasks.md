# Tasks — damaged link targets and WinZip AES members

## 1. Reader

- [x] 1.1 WinZip AES candidates exhausted on integrity raise `CorruptionError`.
- [x] 1.2 Link finalization lists a link with a damaged target, targetless and reported.
- [x] 1.3 Docs: `formats.md`, `extracting.md`, the ZIP handbook.

## 2. Tests

- [x] 2.1 `tests/test_damaged_link_target.py`: ZIP, WinZip AES (one password and
      several), 7z, streaming pass, extraction, strict policy.
- [x] 2.2 `tests/test_zip_aes.py`: a failing HMAC under a candidate list, and colliding
      candidates, raise `CorruptionError`.

## 3. Archive

- [x] 3.1 `openspec archive damaged-link-and-aes-verdicts --yes`
