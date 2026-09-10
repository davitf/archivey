# Tasks — 7z AES tail key check

## 1. The check

- [ ] 1.1 Compute the padding length from the AES coder: `pack_size` minus the coder's
      declared output size in `UNPACK_INFO`. Guard a `pack_size` that is not a multiple of
      16 (malformed) and the multi-packed-stream case the reader already refuses.
- [ ] 1.2 Read the last two ciphertext blocks from the pack view — the coder's IV
      stands in when the stream is one block — and decrypt **one block** with the
      crypto backend's one-shot CBC. Do not wrap the pack view in `AesDecryptStream`
      and skip to EOF: that class has no `seek` and would decode the packed stream.
      Padding length is the AES coder's unpack size vs pack size, never folder
      unpack size (plaintext).
- [ ] 1.3 Return `CONFIRMED` only when the padding is ≥ 4 bytes and all zero. A non-zero
      tail returns nothing, never `REJECTED`; below 4 bytes the check does not run.
- [ ] 1.4 Wire it as the ladder's first rung in `_password_for_folder`, ahead of the
      anchor plan. On `decode_encoded_header`, run it on the pass that already
      materialises the header (see bounded-password-confirmation task 2.5).
- [ ] 1.5 Leave `DIGEST_UNVERIFIABLE` firing on a no-anchor folder even when the tail
      confirmed — the check attests the key, not the data.

## 2. Diagnostic

- [ ] 2.1 Add `check="cheap_key_check"` to `EncryptedVerificationContext`, emitted when a
      member accepted on the tail alone is abandoned before its digest.

## 3. Tests

`bounded-password-confirmation` §5 carries a rule these inherit: each test must fail when
the specific path it names is disabled, not merely when the code is broken. That change's
preamble has the two #318 incidents behind it. This change is unusually exposed to the
trap, because the tail check is a *fast path* — every test below can pass on the slow path
it is supposed to bypass, so "it rejected the wrong password" proves nothing on its own.

- [ ] 3.1 Padding matrix over payload sizes giving padlen 0, 1–3 and ≥ 4, for `Copy` and
      LZMA2: correct candidate confirms at ≥ 4, check is inert below 4 and at 0.
- [ ] 3.2 A fixture whose padding is deliberately non-zero under the correct password,
      proving no candidate is dropped and the ladder still finds it. Build it by patching
      the ciphertext tail of a known archive, not by hoping a writer misbehaves.
- [ ] 3.3 Wrong candidates against a padlen ≥ 4 archive: none confirms, all fall through.
      Assert the tail check reported no match, not just that the open failed — a wrong
      password fails anyway via the anchor, so the weaker assertion holds with the check
      deleted.
- [ ] 3.4 No-anchor folder, several candidates: the right one wins and
      `DIGEST_UNVERIFIABLE` is still emitted.
- [ ] 3.5 Assert the check decodes no folder bytes — spy on the pipeline, as
      `test_password_confirm_does_not_request_the_whole_folder` does for #318. This is the
      only assertion that distinguishes "confirmed by the tail" from "confirmed by the
      anchor"; without it 3.1 and 3.4 both pass with the tail check removed.
- [ ] 3.6 Commit the p7zip and py7zr padding fixtures with a note on which writer and
      version produced each, so the premise in `design.md` has an artefact.
- [ ] 3.7 `./scripts/check.sh && ./scripts/test.sh --all-configs`.

## 4. Record

- [ ] 4.1 Close threat-model **O12**.
- [ ] 4.2 CHANGELOG under Security.
- [ ] 4.3 `docs/formats.md`: 7z now has a password check, with the caveat that it attests
      the key and not the payload.
