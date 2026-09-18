# Tasks — explicit archive offset, gathered AES reads

## 1. The header walk

- [ ] 1.1 Add `_archive_offset(fd: _Readable) -> int` in `rar_parser.py` and use it at the
      four sites that mean "archive offset": `:1125` and `:1152` (RAR3 `header_offset`,
      `data_offset`) and `:1996`, `:2027` (RAR5). Behaviour is unchanged — this is a
      rename with a docstring.
- [ ] 1.2 `_Readable` (`:64`) documents *archive offset* from then on, not "a ciphertext
      `tell`". Fix its stale second claim while there: it says streamtools bases "either
      close the inner stream or have no ciphertext `tell`", and `AesDecryptStream` has
      borrowed since #340 (`owns_inner=False`). The surviving reason is the cursor.

## 2. `AesDecryptStream`

- [ ] 2.1 `read` gathers a short source read (`read_exact(self._source, ask)`) instead of
      handing a partial block to the stage. Delete the disclaimer sentence from the `read`
      docstring — the identity is now unconditional, which is the point.
- [ ] 2.2 Add `cipher_tell()`, returning `_cipher_start + _pos + len(_buf)`.
- [ ] 2.3 Do **not** add a `length=`. `SlicingStream` bounds streams here
      (`sevenzip_reader.py:783`); a second mechanism needs the fold's measurement, not
      this change's convenience.

## 3. Tests

- [ ] 3.1 A source that returns short non-empty reads (a wrapper over `BytesIO` capping
      each `read` at, say, 7 bytes) produces byte-identical plaintext to a full-count
      source. Verify it fails against the pre-2.1 code — a short read there corrupts every
      block after the first, so this should be loudly red, not subtly.
- [ ] 3.2 `cipher_tell()` equals the source's own `tell()` at several mid-block plaintext
      positions, including after a partial `read` that leaves `_buf` non-empty. That
      leftover case is the whole reason the RAR walk needs the accessor.
- [ ] 3.3 The existing RAR parser suite still passes unchanged — especially
      `test_encrypted_header_plaintext_tell_breaks_the_walk`, which pins the `tell()`
      semantics task 1.1 is re-expressing. If it needs editing, 1.1 went wrong.
- [ ] 3.4 `./scripts/check.sh && ./scripts/test.sh --all-configs`.

## 4. Record

- [ ] 4.1 `dev-docs/formats/rar.md:286` describes `_HeaderDecryptStream.tell()` as the
      ciphertext cursor; add that the walk now reads it through `_archive_offset`.
- [ ] 4.2 `dev-docs/topics/stream-ownership.md` (`:19`, `:35`): the `_HeaderDecryptStream`
      row mentions "ciphertext `tell`"; note that `AesDecryptStream` now also exposes one.
- [ ] 4.3 `openspec validate --strict rar-archive-offset-and-aes-cursor`.
