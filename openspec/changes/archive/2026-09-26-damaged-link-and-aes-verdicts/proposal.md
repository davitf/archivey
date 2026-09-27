# Damaged link targets and damaged WinZip AES members

## Why

The S28 re-sweep of the ZIP crypto code found that a damaged WinZip AES member raised
`CorruptionError` with one password and an ambiguous `EncryptionError` with a candidate
list (S28-K4). The split also decided whether an archive could be listed at all: a
symlink whose target data failed its check made `members()` raise for the whole archive
unless the error happened to be the `EncryptionError`. davi ruled (2026-09-26):
`CorruptionError` on both password paths, and a damaged link target must not fail the
listing.

## What Changes

- A WinZip AES member that every candidate passing `pw_verify` fails on integrity raises
  the damage, as the one-password path does: `CorruptionError`, or `TruncatedError` for
  data that ends before its declared size. ZipCrypto keeps its
  ambiguous `EncryptionError`: its check is 8 bits, not 16.
- Link finalization catches `CorruptionError` / `TruncatedError` per link: the link stays
  listed without a target, `SYMLINK_TARGET_UNAVAILABLE` (`reason="target_data_damaged"`)
  says why, and opening or extracting the link raises the fault. RAR3/4 reads link
  targets with no check, so this cannot happen there.

## Impact

- `format-zip` spec: the WinZip AES requirement and the confirmation requirement.
- `archive-reading` spec: a new requirement for damaged link targets.
- A caller catching `EncryptionError` for a damaged AES member under a candidate list now
  sees `CorruptionError`.
