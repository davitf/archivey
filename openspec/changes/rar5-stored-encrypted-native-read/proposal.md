# Read a stored encrypted RAR5 member without `unrar`

## Why

`_can_direct_read` already serves stored, non-solid, unsplit RAR members from a direct
`SharedView` slice, with no subprocess. `not info.is_encrypted` is the only clause
excluding the encrypted ones — and for RAR5 every piece needed to decrypt them is already
parsed and already implemented for the header path:

| Piece | Where it already is |
| --- | --- |
| Per-file salt, IV, 12-byte PswCheck | `_parse_rar5_file_encryption` (RAR5 FILE extra record) |
| AES-256 key derivation | `_rar5_s2k(password, salt, 1 << kdf_count)` — `rar5_hash_key`'s docstring records the offset scheme (Key at `1 << kdf`, HashKey `+16`, PswCheck `+32`) |
| AES-CBC pull stream with seek | `AesDecryptStream` (#342) |

Verified on #342: key + IV + `AesDecryptStream` over the direct slice reproduces `unrar`'s
output byte-for-byte at 7 / 3000 / 3001 byte payloads, and seeks. RAR5 pads `compress_size`
to a 16-byte boundary and trims with `file_size`, exactly like the 7z AES shape the stream
was built for (measured: 3000 → 3008, 3001 → 3008, 7 → 16).

The win is caller-visible: for this member shape, no `unrar` process, no
`PackageNotInstalledError` when the binary is absent, no whole-archive temp copy for a
non-path source, and O(1) seek from the CBC restart instead of a respawn-and-replay.

## What Changes

- `_can_direct_read` SHALL admit **RAR5** stored members whose only disqualifier was
  encryption, decrypting the direct slice natively. The other guards (solid, split,
  volume-spanning) stay.
- **RAR5 only.** `rar_parser.py` sets `file_encryption=None` on the RAR3 path, so RAR4's
  8-byte `LHD` salt is not parsed; RAR4 stored encrypted members keep going through
  `unrar`. Parsing that salt is out of scope here.
- A wrong password SHALL surface as `EncryptionError` from the FILE record's 12-byte
  PswCheck, not as corrupt output. `_check_rar5_password` is written for the ENCRYPTION
  block and is untested against a FILE record — task 1.3.
- Compressed encrypted members, and every member in a solid/split/spanned archive, keep
  using `unrar`. This narrows *when* the binary is needed; it does not remove the
  dependency.

## Impact

- Capabilities: `format-rar`, `packaging-and-extras`.
- Code: `internal/backends/rar_reader.py` (`_can_direct_read`, `_direct_view`),
  `internal/backends/rar_parser.py` (a file-data key helper beside `_tweaked_hash_key`).
  No new stream machinery — `AesDecryptStream` already does the work.
- **Amends ADR [0002](../../../dev-docs/decisions/0002-native-rar-metadata-unrar-data.md)**,
  which scopes native decryption to *headers*. That amendment is part of this change, not a
  side effect of it.
- The UnRAR licence constraint is about the proprietary *compression*; a `-m0` member has
  nothing to decompress, and the KDFs are already ported (ISC, from `rarfile`).
