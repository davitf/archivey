# Tasks — ZipCrypto single-password integrity

## 1. Reader

- [x] 1.1 Wrap the single-password lazy ZipCrypto stream so a candidate integrity failure
      on `read` or `seek` raises `EncryptionError` naming both causes.

## 2. Tests

- [x] 2.1 A single password that collides on the check byte raises that `EncryptionError`
      for every compression method, on `read` and on a forward `seek`, without a
      wrong-password mark; the right password still reads.
- [x] 2.2 Existing single-candidate corrupt-member tests expect the new error.
- [x] 2.3 The wrong-ZIP-password test in `tests/test_password.py` stops depending on the
      archive's random salt.

## 3. Archive

- [x] 3.1 `openspec archive zipcrypto-single-password-integrity --yes`
