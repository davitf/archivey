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
- [ ] 1.4 Do **not** add `resolve_password`. `_PasswordCandidates.attempt` stays the
      candidate loop. The probe returns `ConfirmVerdict` (or raises `EncryptionError`
      on `REJECTED`). Promote `CONFIRMED` always; promote `INCONCLUSIVE` only when
      the set is unambiguous.

## 2. 7z ladder

- [ ] 1.5 Give the probe a compressed-input cap alongside the `CONFIRM_PREFIX_BYTES`
      output target. An output-only bound does not constrain a block-transform codec: 64 KiB
      of bzip2 output costs 905 KB of input (design §2). Size it from
      `_INNER_TAR_MAX_PROBE_BYTES` rather than picking a new number, and decide there
      whether the two probes should share one helper — "decode up to N output bytes from at
      most M input bytes, did the codec object?" is the whole primitive, and
      `_probe_inner_tar` is it with a `ustar` check on the end. That is the maintainer's
      "consider merging with the probing logic" (#319 D1); closing it either way is fine,
      leaving it unasked is not.

- [ ] 2.1 Rewrite `_verify_decoded_folder` onto `plan_confirm` / `run_confirm_plan`.
      Earliest anchor wins; the folder digest is a fallback, not a first test.
- [ ] 2.2 Delete the `if not member_digests` full drain.
- [ ] 2.3 D1-C budget: rejecting codec (LZMA1/LZMA2/BZip2/Deflate) with a late CRC
      stops at `CONFIRM_PREFIX_BYTES`; Copy / PPMd / filters-only walk a late CRC.
      Unbounded pass only when a checksum exists *and* the set is ambiguous. No CRC
      at all: first candidate, `DIGEST_UNVERIFIABLE`, no extra decode.
- [ ] 2.4 Keep `UnsupportedFeatureError` / `PackageNotInstalledError` passthrough and the
      `EncryptionError("Wrong password or corrupt 7z folder")` message.
- [ ] 2.5 Encoded header: apply codec-error → `REJECTED` and (later) the tail check on
      the pass that already materialises the header. Do not `run_confirm_plan` and then
      decode the header again — the decoded bytes *are* the product. Cap is 64 MiB.

## 3. ZIP

- [ ] 3.1 Point `_open_compressed_confirmed` at `plan_confirm` / `run_confirm_plan` as
      the probe under `_finish_password_attempt`. Keep the candidate-failure filter
      (`BadZipFile` “Bad CRC-32…” / `zlib.error` / `lzma.LZMAError` / BZIP2’s
      `OSError("Invalid data stream")`).
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
- [ ] 4.4 `ENCRYPTED_MEMBER_UNVERIFIED` stays **out** of `ARCHIVE_INTEGRITY_CODES`
      (D2-C). Record the reason next to `STREAM_REWIND_REDECOMPRESSES`. Revisit when
      `stream.verified` lands. Preset-matrix row: `strict()` + ZipCrypto peek → no raise.

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

- [ ] 5.1 Red-green on the ladder: a 200 MiB solid fixture generated in tmp (do not
      commit it) where confirmation must decode only the first member. Assert bytes
      decoded, not wall time — and verify the assertion fails with the anchor plan
      forced to walk the whole folder, not merely with `_verify_decoded_folder`
      broken. A test that only notices total breakage would pass against the
      pre-change reader, which is the thing it exists to distinguish from. Separate
      cases for LZMA2 (prefix, not 200 MiB) and Copy with CRC at EOF (does walk).
- [ ] 5.2 Codec-rejection evidence: random input to LZMA1, LZMA2, BZip2, Deflate,
      **and** Deflate64, ZSTD, Brotli, LZ4, PPMd, Delta, BCJ. The first four must
      reject; filters must not; the rest decide whether they join the rejecting list
      or stay with Copy. So §2 of the design stays true if a dependency changes.
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
- [ ] 6.4 Implementation PR: the ladder *why* on `dev-docs/formats/` (7z page), not
      only in this `design.md`.
