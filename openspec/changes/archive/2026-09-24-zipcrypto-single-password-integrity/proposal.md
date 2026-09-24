# Report a lone ZipCrypto password's integrity failure as a password error

## Why

Traditional ZipCrypto checks a password against one byte of its 12-byte header, so about
one wrong password in 256 passes that check and decrypts to garbage. With several
candidates the reader confirms each one before accepting it, and a failure is reported as
an `EncryptionError` that says the password may be wrong or the member corrupt. With one
password there is no confirmation read (by design: it would cost a second pass for
nothing to choose between), and the garbage surfaces on the caller's read as a CRC or
decompressor failure, which the ordinary path reports as `CorruptionError`. A caller who
typed the wrong password is then told the archive is damaged.

CI caught this as a flaky test: roughly two in six hundred freshly built archives let the
test's wrong password through the check byte.

## What Changes

- On the single-password lazy path, a read-time candidate integrity failure (the same
  set the confirmation path treats as a candidate failure: CRC mismatch, `zlib.error`,
  `lzma.LZMAError`, BZIP2's `OSError("Invalid data stream")`) SHALL raise
  `EncryptionError` naming both causes, on `read` and on a forward `seek`.
- The error is not a wrong-password verdict: nothing in the archive can tell a colliding
  wrong password from a damaged member read with the right one, so it carries no
  wrong-password mark.
- Stays lazy: still no confirmation read for one candidate.
- The error carries an internal mark, so the ZIP symlink hook reports a target that
  fails this way as `SYMLINK_TARGET_UNAVAILABLE` with reason `"password_or_damage"`
  and a message naming both causes, not as `"password_required"`. That also covers the
  multi-candidate ambiguous error, which reached the hook the same way before.
- Unchanged: structural `BadZipFile`, WinZip AES members, unencrypted members, and a
  caller stream opened after a multi-candidate confirmation, where the password is known
  good and a later failure is still `CorruptionError`.

## Impact

- `format-zip` spec: the multi-candidate confirmation requirement's one-candidate
  sentence and its matrix, and the symlink sentence and row in the metadata
  requirement.
- `diagnostics` spec: the encrypted-symlink row names the new reason.
- Docs: the `EncryptionError` row in `errors-and-diagnostics.md`, and a `gotchas.md`
  entry.
- `archivey.internal.backends.zip_reader`: the lazy path wraps the member stream.
- A right password on a damaged ZipCrypto member now reports `EncryptionError` rather
  than `CorruptionError`; the message says the member may be corrupt.
