# List the members before a cut inside a RAR header

## Why

A RAR file that ends part-way through a header used to raise `CorruptionError` at open
and list nothing, even for the members it held in full. `unrar` 7.00 lists those members
and then reports an unexpected end of archive. TAR already lists the prefix and raises
`TruncatedError` during iteration, and `format-tar` says so. `format-rar` said nothing
about a cut inside a header, so this contract lived only in `docs/formats.md` and
`dev-docs/formats/rar.md`.

## What changes

A cut inside a plain header, or inside an encrypted header the walk can tell from a wrong
key, opens and lists the members before the cut; `TruncatedError` follows the listing.
A declared header size that is invalid while its bytes are present stays
`CorruptionError`. The boundary cases keep their meaning (`dev-docs/formats/rar.md` §1).

## Impact

- `format-rar`: one added requirement with its matrix.
- Code: `rar_parser.py` (the header readers and both walks).
