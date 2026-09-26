# Tasks — sticky content verdicts

## 1. Stream

- [x] 1.1 `ArchiveStream` stores the first content verdict and re-raises it from `read`
      and `seek`, with its first traceback.
- [x] 1.2 ADR 0014 bullet and `docs/errors-and-diagnostics.md`.

## 2. Tests

- [x] 2.1 `tests/test_member_stream_contract.py`: the verdict keeps raising after a seek
      back, for STORED and DEFLATE, and its traceback does not grow across retries.
- [x] 2.2 The same for a ZipCrypto verdict and a WinZip AES HMAC mismatch.

## 3. Archive

- [x] 3.1 `openspec archive sticky-content-verdicts --yes`
