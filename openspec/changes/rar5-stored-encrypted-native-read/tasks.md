# Tasks — stored encrypted RAR5 native read

## 1. Key and password

- [ ] 1.1 Add a file-data key helper beside `_tweaked_hash_key` in `rar_reader.py` (or
      export one from `rar_parser.py` next to `rar5_hash_key`) returning
      `_rar5_s2k(password, salt, 1 << kdf_count)` — the **AES key**, at `1 << kdf`, not
      the HashKey at `+16`. Reuse the `_RAR_MAX_KDF_SHIFT` guard; a `kdf_count` past it is
      `CorruptionError`, as in `rar5_hash_key`.
- [ ] 1.2 Decide the caching shape against the **measured** numbers, not a guess. Real
      archives written by RARLAB `rar` get `kdf_count=15` — 32 768 iterations, one
      PBKDF2 pass measured at 8–22 ms on this container (two runs, different load;
      milliseconds, not the ~0.1 s an earlier draft of this task claimed — 0.1 s is
      roughly `kdf_count` 18–19). The count is what dominates: spying on `_rar5_s2k`
      while opening one stored encrypted member **today** gives
      `[32800, 32784, 32800, 32784]` — four passes, because `_tweaked_verify_spec`
      (`rar_reader.py:1105`) is evaluated twice on the spawn path (once at `:1324` for
      `has_hash`, once via `_wrap_payload_stream`) and each evaluation runs
      `_check_rar5_password` (`+32`) and `rar5_hash_key` (`+16`). The direct path skips
      the `has_hash` site, so it lands at **three** passes per member: the new AES key at
      `1 << kdf`, plus `+16` and `+32`. UnRAR derives one chain and reads all three off
      it at offsets 0 / +16 / +32. Per-member derivation is acceptable to start; if a
      cache lands, key it on `(password, salt, kdf_count)` — salts differ per member, so
      an archive-level cache like `_Rar5HdrEnc.cached_key` would be wrong.
      **Maintainer decision (davitf, 2026-09-18,
      [#347](https://github.com/davitf/archivey/pull/347#issuecomment-5724553004)):**
      record the numbers here; the dedup itself — one PBKDF2 pass yielding all three
      values, and removing the duplicate `_tweaked_verify_spec` evaluation — is **ARC-54**
      and is not this change's job.
- [ ] 1.3 **Confirm `_check_rar5_password` accepts a FILE `CRYPT` check value**, with a
      fixture pair: correct password returns `True`, wrong password raises
      `EncryptionError`. `design.md` §"PswCheck on a FILE record" says why this is an
      assumption today. If it does not hold, fall back to digest-only detection and say
      so in the spec rather than shipping a check that silently returns `False`.
      While here, fix the message it raises: `rar_parser.py:2089` says
      `"Wrong password for RAR5 header encryption"`, so a typo'd password on a `-p` (not
      `-hp`) archive would tell the user the *header* password is wrong for an archive
      whose headers are not encrypted. Parameterise it, or raise from the caller.

## 2. The read path

- [ ] 2.1 Split `_can_direct_read`'s encryption clause: allow `info.is_encrypted` when
      `info.file_encryption is not None` **and** a crypto backend is available **and** a
      password is available. Keep `file_solid`, `split_after`, `split_before`,
      `spanned_volumes` and the `_RAR_METHOD_STORED` check untouched.
- [ ] 2.2 In `_open_member`, open the ciphertext view over **`compress_size`**
      (`_direct_view(raw, raw.compress_size)`), wrap it in `AesDecryptStream`, and trim to
      `file_size` with
      `SlicingStream(AesDecryptStream(...), length=raw.file_size, owns_inner=True)` —
      the shape `sevenzip_reader.py:783` already uses, which preserves `seek` over a
      seekable source so the spec's seek row still holds. `AesDecryptStream.__init__`
      (`crypto.py:230`) takes `(source, params, *, owns_inner)` and has **no** `length=`;
      `_wrap_payload_stream`'s `expected_size` *verifies* a length rather than truncating
      one, so leaning on it hands the caller 3008 bytes and a spurious failure. Taking the
      length from the view hands the caller the CBC padding — `design.md` has the measured
      table.
- [ ] 2.3 Raise `EncryptionError` at open when the FILE record's PswCheck rejects the
      password, before any plaintext is produced.
- [ ] 2.4 Confirm the direct branch still goes through `_wrap_payload_stream`, so a
      natively decrypted member is digest-verified by `_tweaked_verify_spec` exactly as
      the spawned read is. This is expected to be free; assert it rather than assume it.
- [ ] 2.5 When neither a crypto backend nor a RARLAB binary is present, raise
      `PackageNotInstalledError` naming **both** routes. Do this by catching and
      re-raising at the `_open_member` call site, which is the only place that knows both
      the member shape and whether the gate declined for want of a backend — **not** by
      teaching `rar_unrar.py` about `cryptography`. The message today is the module
      constant `_NOT_INSTALLED_MSG` (`rar_unrar.py:97`, raised at `:182`, `:262`, `:263`,
      `:502`); it names RARLAB only and has no way to know the member shape, so leaving
      the work there would either ship a scenario nothing implements or push crypto
      availability down into the spawn layer. With a binary but no backend, fall through
      to the spawn unchanged.

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
      are native. Only the **Decision** paragraph needs the edit ("Decompress member
      **data** via the RARLAB `unrar` binary") — the Consequences bullet already reads
      "reading *compressed* members requires it on `PATH`" and is correct as written.
      Note the amending change id.
- [ ] 4.2 `docs/formats.md` has **three** sites, not one: the format-table cell at `:16`
      (`**`unrar` or `rar` binary for data**`), the bold line at `:22` ("RAR member data
      needs RARLAB `unrar` or `rar` …"), and the "Member **data**" bullet at `:128`.
- [ ] 4.2b `dev-docs/formats/rar.md` states the rule this change moves, and is one of the
      handbook pages already rewritten, so it is current and worth keeping current:
      the §"Three routes" table row **Direct slice | Stored (`-m0`), unencrypted, …**
      (`:387`) becomes wrong for RAR5, and the "Core dependencies" row (`:18`) is in the
      same neighbourhood. Add a sentence on why RAR4 stays on the spawn — the `LHD` salt
      is not parsed — so the asymmetry reads as a decision rather than an oversight.
- [ ] 4.3 CHANGELOG.
- [ ] 4.4 `openspec validate --strict rar5-stored-encrypted-native-read`.
