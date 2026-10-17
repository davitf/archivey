## 1. Read path

- [x] 1.1 `rar5_data_key` beside `rar5_hash_key`: the AES key at `1 << kdf_count`,
      bounded by `_RAR_MAX_KDF_SHIFT`
- [x] 1.2 `_natively_decryptable`: stored, encrypted, one part, ciphertext sizes a
      whole number of AES blocks covering the unpacked size; RAR 2.9-4 with a salt and
      extract version 29+, RAR5 with an AES-256 record and a 16-byte IV
- [x] 1.3 `_open_decrypted_stored`: direct view over `compress_size`,
      `AesDecryptStream`, `SlicingStream` to `file_size`, `_watch_unverified`,
      `_wrap_payload_stream`
- [x] 1.4 Password choice: RAR5 PswCheck; otherwise the CRC check over a native
      decryption (`_stored_check`, now RAR5 too), or the first candidate
- [x] 1.5 Without `cryptography`: the program reads it; with no program either, the
      error names both; under `none`, `PackageNotInstalledError` naming `cryptography`
- [x] 1.6 Split RAR5 members take the last part's encryption record with its digest

## 2. Tests and record

- [x] 2.1 `tests/test_rar_stored_encrypted.py`: sizes 0/7/16/3000/3001 in RAR4 and RAR5
      against the written bytes and `unrar p`, no process started, seeks, wrong and
      missing passwords, password lists, damaged ciphertext, the fallback matrix
- [x] 2.2 ADR 0002, `RarDecompressor` docstring, user docs and handbook
- [x] 2.3 `openspec validate --strict rar-stored-encrypted-native-read`
- [x] 2.4 `openspec archive rar-stored-encrypted-native-read --yes`
