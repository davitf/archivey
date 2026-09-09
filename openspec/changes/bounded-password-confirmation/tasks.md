# Tasks — bounded password confirmation

## 1. Shared confirmation vocabulary

- [ ] 1.1 `ConfirmVerdict` (`REJECTED` / `CONFIRMED` / `INCONCLUSIVE`) and `ConfirmPlan`
      in `internal/password_confirm.py`; keep the module free of format types.
- [ ] 1.2 `plan_confirm(substreams, tail_crc, *, budget, min_verified_bytes=4)` — earliest
      sufficient anchor, terminating once CRC-verified bytes reach 4. Unit-test the short-
      member cases directly (1-byte member, CRC-less first member, folder digest plus
      member CRCs, no anchor at all).
- [ ] 1.3 `run_confirm_plan(stream, plan)` — chunked read, rolling CRC, short read is
      `REJECTED`. Keeps PR #318's memory property; reuse its chunk size.
- [ ] 1.4 `resolve_password(passwords, member, probe, *, accept_inconclusive)` — static
      candidates, then provider; first `CONFIRMED` wins; owns the winner /
      ambiguous-failure / password-required outcomes.

## 2. 7z ladder

- [ ] 2.1 Rewrite `_verify_decoded_folder` onto `plan_confirm` / `run_confirm_plan`.
      Earliest anchor wins; the folder digest is a fallback, not a first test.
- [ ] 2.2 Delete the `if not member_digests` full drain.
- [ ] 2.3 Budget behaviour for a chain with no anchor in reach: bounded prefix for a
      rejecting codec; for `Copy` / PPMd, the unbounded pass only when
      `_passwords.is_ambiguous()`.
- [ ] 2.4 Keep `UnsupportedFeatureError` / `PackageNotInstalledError` passthrough and the
      `EncryptionError("Wrong password or corrupt 7z folder")` message.
- [ ] 2.5 Apply the same ladder to `decode_encoded_header`, which decodes the whole header
      folder per candidate today.

## 3. ZIP

- [ ] 3.1 Move `_open_compressed_confirmed` onto `resolve_password`.
- [ ] 3.2 Leave the STORED shared ciphertext pass where it is; check no behaviour moved.
- [ ] 3.3 Name ZipCrypto's check byte and WinZip AES's `pw_verify` as the cheap-key-check
      rung in code comments so the ladder reads the same in both backends.

## 4. `ENCRYPTED_MEMBER_UNVERIFIED`

- [ ] 4.1 Code, `EncryptedVerificationContext`, policy wiring, `to_dict()` JSON-safety.
- [ ] 4.2 Emit from both readers at close, only when the digest was not reached *and* the
      password was accepted below digest strength. Assert silence for a partial read whose
      password was anchor-confirmed.
- [ ] 4.3 Docs: `docs/access-and-cost.md`, `docs/formats.md`, `docs/extracting.md` —
      replace #318's "wall time still tracks folder size × candidates" text.

## 5. Tests

**Every test in this section must fail when the *specific* path it names is disabled, not
merely when the code is broken somehow.** Name the mutation in a comment next to the
assertion, and run it before believing the test.

This is not a general plea for rigour; #318 shipped the same defect twice in two rounds,
and both were invisible to a green suite:

1. `test_7z_multi_password_rejects_wrong_candidate_via_crc` monkeypatched
   `SevenZipReader._open_folder_pipeline`. Production calls the module-level
   `open_folder_pipeline`; the method existed only so tests could patch it. The fake never
   ran, and the test passed on the unmodified reader. Raising inside the fake still left it
   green.
2. Patching the right function fixed that, and the test was still wrong. The assertion was
   `assert called` — proof the fake ran, not proof it served garbage. When the fake declines
   (its folder matcher keys on a member size that happens to be 13 bytes), the wrong
   candidate is rejected by **LZMA**, the archive still reads correctly, and `called` is
   satisfied. The test claims to pin the CRC-only path and was passing via codec rejection.
   Fixed by recording inside the branch: `assert garbage_served`.

The shape both share: an assertion on *machinery* (was the hook called, did the code run)
rather than on *the path under test*. Machinery assertions are satisfied by the cheap path
the test exists to avoid. Assert the effect, and prove it by disabling the mechanism that
produces it.

- [ ] 5.1 Red-green on the ladder: a 200 MiB solid fixture where confirmation must decode
      only the first member. Assert bytes decoded, not wall time — and verify the assertion
      fails with the anchor plan forced to walk the whole folder, not merely with
      `_verify_decoded_folder` broken. A test that only notices total breakage would pass
      against the pre-change reader, which is the thing it exists to distinguish from.
- [ ] 5.2 Regenerate the codec-rejection evidence as a test (random input to each raw
      decompressor 7z can chain), so §2 of the design stays true if a dependency changes.
- [ ] 5.3 Partial-read diagnostic in both readers, including the brute-forced colliding
      ZipCrypto password (commit the fixture and the password — finding it takes ~300
      tries, but do not brute-force in CI). Verify the ZipCrypto case fails when the
      diagnostic is suppressed *and* that it is the colliding password reaching the read,
      not a password rejected at the check byte — the second is the §5-preamble trap.
- [ ] 5.4 Store+AES ambiguous with the only anchor at folder end: the correct candidate
      still wins. Verify it fails when the anchor pass is skipped, not only when
      confirmation is removed entirely.
- [ ] 5.5 `./scripts/check.sh && ./scripts/test.sh --all-configs`.

## 6. Record

- [ ] 6.1 Threat model **O12**: record what this change bounds and name the one shape it
      leaves open (`Copy`/PPMd, anchor at folder end, ambiguous candidates), pointing at
      `sevenzip-aes-tail-key-check`. O12 does not close here.
- [ ] 6.2 CHANGELOG under Security.
- [ ] 6.3 Cross-reference the `stream.verified` idea in `dev-docs/IDEAS.md` from the
      diagnostic's docstring, so whoever builds it knows the code is the thing to retire.
