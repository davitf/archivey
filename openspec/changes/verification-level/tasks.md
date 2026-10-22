# Tasks — verification level

## 1. Sources

- [ ] 1.1 `MemberVerifier`: sticky `ended_clean` (set at the end of a `_conclude` that
      did not raise) and `digest_matched` (set after `_verify_digests` passes); `_rearm`
      keeps both.
- [ ] 1.2 `StreamCodec.checks_content` (default `False`; `True` for gzip, bzip2, lzip,
      zlib). `open_codec_stream` passes it to its `ArchiveStream`.
- [ ] 1.3 `UnverifiedPasswordReadWatch` takes the key check and answers "key in doubt".
- [ ] 1.4 `WinZipAesDecryptStream` and `WinZipAesForwardStream` answer "content matched"
      from `_authenticated`.

## 2. The query

- [ ] 2.1 A private `_verification_proofs()` query and `ask_verification_proofs(stream)`
      helper, next to `ask_digest_intact`.
- [ ] 2.2 `DelegatingStream.forwards_verification` (default `False`: forward only the
      key doubt). Turn it on for the counting and locking wrappers, the password watch,
      the ZipCrypto wrapper and the codec end-check wrappers.
- [ ] 2.3 `VerifyingStream` answers from its verifier. `DecompressorStream` forwards
      only a content match from its source.
- [ ] 2.4 `ArchiveStream`: end-of-stream flag on the read path, seek tracking for the
      codec rule, the codec flag adopted on collapse, `verified` and `verifiable`.

## 3. Public surface

- [ ] 3.1 `archivey.Verification` in `types.py`, exported and listed in the API tests.
- [ ] 3.2 Docstrings on the two properties.

## 4. Tests

- [ ] 4.1 The spec matrix, per format, by `open()` and `stream_members()`.
- [ ] 4.2 Weak-key matrix: every `ENCRYPTED_MEMBER_UNVERIFIED` site reports `NONE` after
      a partial read.
- [ ] 4.3 No I/O from reading the properties (a counting source).

## 5. Docs and records

- [ ] 5.1 `docs/errors-and-diagnostics.md`: replace "No option makes archivey check a
      member before handing you its bytes" with the read-to-the-end rule and the level.
- [ ] 5.2 `docs/reading.md`: seeks and the level.
- [ ] 5.3 ADR 0014: the `STRICT` pointer becomes the level.
- [ ] 5.4 `dev-docs/IDEAS.md`: the entry becomes `verify()` only.
- [ ] 5.5 Archive `verification-integrity-mode` as closed, not implemented.
- [ ] 5.6 Diagnostics spec: the `ENCRYPTED_MEMBER_UNVERIFIED` exclusion note's "revisit
      when `stream.verified` lands" stays true, since the code stays.
