# gzip accelerator: a wrong last ISIZE behind zero padding

## Why

The `gzip_accel` fuzz target found a one-member gzip with a wrong ISIZE, followed by a
zero byte, whose last four bytes equal the length decoded. The ISIZE backstop reads the
last four bytes of the file, so it passes; rapidgzip checks the CRC-32 but not the ISIZE;
the standard library raises "incorrect length check". The data is right, since the
CRC-32 confirms it. This is the difference the spec already accepts for a member before
the last: a malformed trailer, not damaged data.

Telling padding from a trailer that ends in zero bytes would need the CRC-32 of the
output, or a second decode, for every file that ends in a zero byte: every gzip whose
content is under 16 MiB, under `ON`.

## What changes

The spec names this case as accepted, and the `gzip_accel` target accepts it when the
accelerated output equals what zlib decodes with the length checks left out.

## Impact

- `compressed-streams`: one item in the list of accepted differences.
- `seekable-decompressor-streams`: one matrix row points at it.
- `dev-docs/formats/gzip.md`: the sharp-edge row.
