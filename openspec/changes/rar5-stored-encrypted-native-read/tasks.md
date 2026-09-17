# Tasks — stored encrypted RAR5 native read

## 1. Key and password

- [ ] 1.1 Add a file-data key helper beside `_tweaked_hash_key` in `rar_reader.py` (or
      export one from `rar_parser.py` next to `rar5_hash_key`) returning
      `_rar5_s2k(password, salt, 1 << kdf_count)` — the **AES key**, at `1 << kdf`, not
      the HashKey at `+16`. Reuse the `_RAR_MAX_KDF_SHIFT` guard; a `kdf_count` past it is
      `CorruptionError`, as in `rar5_hash_key`.
- [ ] 1.2 Decide the caching shape: per-member derivation is acceptable to start, but
      record it — PBKDF2 at `1 << kdf_count` is ~0.1 s, so a loop over many small
      encrypted members pays it once per member. If a cache lands, key it on
      `(password, salt, kdf_count)`; salts differ per member, so an archive-level cache
      like `_Rar5HdrEnc.cached_key` would be wrong.
- [ ] 1.3 **Confirm `_check_rar5_password` accepts a FILE `CRYPT` check value**, with a
      fixture pair: correct password returns `True`, wrong password raises
      `EncryptionError`. `design.md` §"PswCheck on a FILE record" says why this is an
      assumption today. If it does not hold, fall back to digest-only detection and say
      so in the spec rather than shipping a check that silently returns `False`.

## 2. The read path

- [ ] 2.1 Split `_can_direct_read`'s encryption clause: allow `info.is_encrypted` when
      `info.file_encryption is not None` **and** a crypto backend is available **and** a
      password is available. Keep `file_solid`, `split_after`, `split_before`,
      `spanned_volumes` and the `_RAR_METHOD_STORED` check untouched.
- [ ] 2.2 In `_open_member`, open the ciphertext view over **`compress_size`**
      (`_direct_view(raw, raw.compress_size)`), wrap it in `AesDecryptStream`, and trim to
      `file_size`. Taking the length from the view hands the caller the CBC padding —
      `design.md` has the measured table.
- [ ] 2.3 Raise `EncryptionError` at open when the FILE record's PswCheck rejects the
      password, before any plaintext is produced.
- [ ] 2.4 Confirm the direct branch still goes through `_wrap_payload_stream`, so a
      natively decrypted member is digest-verified by `_tweaked_verify_spec` exactly as
      the spawned read is. This is expected to be free; assert it rather than assume it.
- [ ] 2.5 When neither a crypto backend nor a RARLAB binary is present, raise
      `PackageNotInstalledError` naming **both** routes. With a binary but no backend,
      fall through to the spawn unchanged.

## 3. Tests

- [ ] 3.1 **Oracle comparison** against `unrar p` for a stored encrypted RAR5 member, over
      payload lengths that are and are not multiples of 16 (at least 7, 16, 3000, 3001).
      This is the test that catches a HashKey/AES-key mix-up — a wrong key produces
      plausible bytes that only a byte-for-byte compare rejects.
- [ ] 3.2 Assert **no subprocess is spawned**: patch/spy `open_unrar_p` and show it is not
      called for this member shape. Without this, every test below passes with the change
      reverted, because the spawn returns the same bytes.
- [ ] 3.3 Seek mid-member, read to EOF, compare against the same slice of the oracle
      output. Include a seek backwards, and a seek past the last block boundary.
- [ ] 3.4 Wrong password: `EncryptionError`, no plaintext. Assert the error type — a
      wrong password fails through the digest path too, so a bare "it raised" assertion
      holds with the PswCheck check deleted.
- [ ] 3.5 Fallback matrix, each asserting which route ran, not just that bytes came back:
      RAR4 stored encrypted, RAR5 compressed encrypted, stored encrypted inside a
      solid/split/spanned archive, and RAR5 stored encrypted with the crypto backend
      unavailable — all spawn. RAR5 stored encrypted with a backend — does not.
- [ ] 3.6 Tweaked-digest verification still fires on the native path: a member whose
      ciphertext has been patched raises `CorruptionError`, not silent bytes.
- [ ] 3.7 Header-encrypted RAR5 (`-hp`) containing a stored member: confirm the FILE
      `CRYPT` record is present and the member is served natively. If RARLAB writes no
      per-file record in that mode, drop the matching spec row instead of forcing it.
- [ ] 3.8 A stored encrypted member read with **no** RARLAB binary on `PATH` (patch the
      lookup) returns data — the caller-visible win, and the one case that cannot be
      faked by the spawn.
- [ ] 3.9 `./scripts/check.sh && ./scripts/test.sh --all-configs`.

## 4. Record

- [ ] 4.1 Amend ADR
      [0002](../../../dev-docs/decisions/0002-native-rar-metadata-unrar-data.md): the
      boundary is *decompression*, not *data*; stored members, encrypted RAR5 included,
      are native. Note the amending change id.
- [ ] 4.2 `docs/formats.md`: RAR member data needs `unrar` except stored members; RAR5
      stored encrypted members need `[recommended]` instead.
- [ ] 4.3 CHANGELOG.
- [ ] 4.4 `openspec validate --strict rar5-stored-encrypted-native-read`.
