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
  `ConfirmVerdict` (`REJECTED` / `CONFIRMED` / `INCONCLUSIVE`), an anchor planner, a
  chunked plan runner, and a candidate driver that owns the winner / ambiguous-failure /
  password-required outcomes. Confirmation is stated as a **rejection filter** — the
  authoritative digest still runs on the caller's own stream — which is what
  `2026-07-11-zip-multipassword-disambiguation` already decided for ZIP and 7z never
  followed.
- **A `cheap_key_check` rung** above the integrity anchor: an O(1) check that confirms the
  key without touching payload data. ZIP's two already exist (ZipCrypto's check byte,
  WinZip AES's `pw_verify`) and are named; 7z gets one for the first time, from the
  zero-padding at the tail of its AES-CBC packed stream. CBC lets that be read as 32 bytes
  at EOF. Confirm-only, never reject, and only where the padding is ≥ 4 bytes.
- **7z confirm stops at the first sufficient integrity anchor** — earliest anchor wins,
  member or folder, stopping once CRC-verified bytes reach 4 — instead of walking the
  folder. The no-anchor drain is deleted.
- **A budget for the case with no anchor in reach.** Compressed folders need none: every
  codec 7z uses rejects a wrong AES key inside the first decoded byte. Store and PPMd get
  the full pass only when the candidate set is ambiguous; when it is not, confirm stops at
  the budget and the member digest is authoritative.
- **`ENCRYPTED_MEMBER_UNVERIFIED`**, in both readers: emitted when a member from an
  encrypted unit is closed before its declared digest is reached *and* the password behind
  it was accepted on a check weaker than that digest. Not on ordinary partial reads.
- ZIP's compressed-confirm path moves onto the shared driver. Its STORED lockstep CRC pass
  stays where it is.

## Impact

- Capabilities: `archive-reading`, `format-7z`, `format-zip`, `diagnostics`.
- Code: `internal/password_confirm.py`, `internal/backends/sevenzip_reader.py`,
  `internal/backends/zip_reader.py`, `diagnostics.py`.
- Behaviour: a wrong single password on an encrypted 7z folder with no anchor in budget
  now surfaces on the caller's read rather than at open. Adding a diagnostic code is not
  purely additive — a `RAISE` default starts raising on an event working programs never
  saw. Both are why this is a change and not a patch on #318.
- Closes threat-model **O12**'s residual. Does not depend on #318, but assumes it merged.
