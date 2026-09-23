# Cap the total key-derivation work an archive can ask for

## Why

RAR5 and 7z store the cost of turning a password into a key in the archive: RAR5's
`kdf_count` asks for `2**kdf_count` PBKDF2-HMAC-SHA256 rounds, 7z's `NumCyclesPower` for
`2**n` SHA-256 rounds. Each is already refused above `2**24`, which bounds **one**
derivation at a few seconds. Nothing bounded the total. RAR5 salts each encryption record
and 7z each folder, so a crafted archive can make every member cost a fresh maximum-cost
derivation, and a candidate password list multiplies that again. The work runs in
`hashlib` with the GIL released and cannot be interrupted; the caller sees a process that
stops responding. Threat-model O18.

The maintainer ruled on 2026-09-19 (review hub, thread R2-K8) for cache and dedupe plus a
total budget on `DecoderLimits`, and on 2026-09-23 set the default at `2**27` rounds, with
`2**24` for the stricter preset that is still to come.

## What changes

- `DecoderLimits.max_key_derivation_rounds`, default `2**27`, `None` in `UNLIMITED`.
- One budget per reader, shared by its key caches, charged on each cache miss before the
  derivation runs. Hits are free, so an ordinary archive (one salt per archiving run)
  spends one or two derivations whatever its size.
- A spent budget raises `ResourceLimitError`. The RAR header walks, which re-wrap other
  failures as `EncryptionError` so candidate iteration continues, let it through.
- ZIP AES is not counted: its 1000 rounds are fixed by the AE spec, not declared.

## Impact

- `archive-reading`: the configuration schema gains the field and its rule.
- `error-handling`: the `ResourceLimitError` row names key-derivation work.
- Code: `archivey/config.py`, `internal/config.py`, `internal/backends/rar_parser.py`,
  `rar_reader.py`, `sevenzip_reader.py`, `sevenzip_aes.py`.
