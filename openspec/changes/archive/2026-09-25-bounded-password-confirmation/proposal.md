# Bounded password confirmation, shared across ZIP and 7z

## Why

7z AES carries no password check value, so a candidate is judged by decoding data and
CRCing it. `_password_for_folder.confirm` does that over the **whole folder**, and it
does it on every first member open, single password or not:

- The correct password always pays a full folder decode, then the folder is decoded a
  second time to serve the member. On a 200 MiB solid LZMA2 folder whose first member is
  4 KiB, `open()` + `read(1)` costs 0.471 s where the first anchor would settle it in
  0.118 s — of which 0.114 s is key derivation.
- `_verify_decoded_folder` checks `folder.digest_defined` before per-member CRCs, so
  where a folder carries both it anchors at the folder end and discards every earlier
  anchor.
- Its no-anchor branch decodes the entire folder and checks only length.
- 7z confirms even when the candidate set is unambiguous; ZIP already gates on
  `is_ambiguous()`.

PR #318 bounded the **memory** of that decode. The work is still unbounded, and threat
model **O12** records it as open.

Separately, both readers share a hole. For ciphers whose only real verifier is the
trailing CRC — ZipCrypto's one header check byte, 7z AES's nothing — a wrong password can
be accepted and an abandoned partial read never reaches the digest. Reproduced on both:
`read(1)` returns garbage with no error; only a full read fails.

## What Changes

- **A shared confirmation ladder** in `internal/password_confirm.py`: a three-valued
  `ConfirmVerdict` (`REJECTED` / `CONFIRMED` / `INCONCLUSIVE`), an anchor planner, and a
  chunked plan runner. The candidate loop stays `_PasswordCandidates.attempt`;
  confirmation is the probe. Confirmation is a **rejection filter** — the
  authoritative digest still runs on the caller's own stream — which is what
  `2026-07-11-zip-multipassword-disambiguation` already decided for ZIP and 7z never
  followed.
- **A `cheap_key_check` rung** named in the ladder above the integrity anchor: an O(1)
  check that confirms the key without touching payload data. ZIP's two already exist
  (ZipCrypto's check byte, WinZip AES's `pw_verify`) and are named here. 7z has none in the
  format; giving it one is `sevenzip-aes-tail-key-check`, split out because it rests on an
  empirical premise about writer behaviour rather than on the format, and should be
  reviewable and revertible on its own.
- **7z confirm stops at the first sufficient integrity anchor** — earliest anchor wins,
  member or folder, stopping once CRC-verified bytes reach 4 — instead of walking the
  folder. The no-anchor drain is deleted.
- **A budget that does not ignore a CRC a non-rejecting chain still needs.** The budget
  is `CONFIRM_PREFIX_BYTES` (64 KiB of decoded plaintext), not one byte. A rejecting
  codec (LZMA1/LZMA2/BZip2/Deflate) with a late CRC stops at the prefix: a wrong key
  dies in the decompressor, a survivor is `INCONCLUSIVE`. Copy, PPMd, and filter-only
  chains walk a late CRC. With no CRC at all, nobody runs an unbounded decode just to
  find that out.
- **`ENCRYPTED_MEMBER_UNVERIFIED`**, in both readers: emitted when a member from an
  encrypted unit is closed before its declared digest is reached *and* the password behind
  it was accepted on a check weaker than that digest. Not on ordinary partial reads. Out
  of `ARCHIVE_INTEGRITY_CODES`; revisit when `stream.verified` lands.
- ZIP's compressed-confirm path uses `plan_confirm` / `run_confirm_plan` as the probe
  under `_PasswordCandidates.attempt`. Its STORED lockstep CRC pass stays where it is.

## Impact

- Capabilities: `archive-reading`, `format-7z`, `format-zip`, `diagnostics`.
- Code: `internal/password_confirm.py`, `internal/backends/sevenzip_reader.py`,
  `internal/backends/zip_reader.py`, `diagnostics.py`.
- Behaviour: a rejecting codec with a late CRC no longer walks the folder on the
  correct password (prefix only). A wrong single password on store+AES *with no CRC*
  surfaces on the caller's read rather than at open; with a CRC at EOF it still fails
  at open. Adding a diagnostic code is not purely additive — `pedantic()` / a
  `default=RAISE` policy starts raising on an event working programs never saw;
  `strict()` does not, the code is out of `ARCHIVE_INTEGRITY_CODES`. Both are why this
  is a change and not a patch on #318.
- Closes most of threat-model **O12**'s residual. What survives is one shape — a `Copy` or
  PPMd chain whose only anchor is at the folder end, with an ambiguous candidate set —
  which still re-reads the packed stream once per candidate. `sevenzip-aes-tail-key-check`
  closes that; O12 stays open until it lands.
- Does not depend on #318, but assumes it merged.
