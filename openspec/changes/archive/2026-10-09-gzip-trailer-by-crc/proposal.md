# gzip accelerator: find the trailer by the CRC-32, not by the file's last four bytes

## Why

The ISIZE backstop took the last four bytes of the file for the ISIZE. Bytes appended after
a member with a wrong ISIZE, equal to the length decoded, passed for it: rapidgzip checks
the CRC-32 but not the ISIZE, so the accelerator returned the payload where the standard
library raises "incorrect length check" (found by the `gzip_accel` fuzz target; test in
#643). `gzip-padded-isize-accepted` had accepted the zero-padding form of this as too costly
to tell apart.

## What changes

The backstop keeps a CRC-32 of the output, from offset 0, as `_ZlibAdlerCheckStream` keeps
an Adler-32 (the shared `_OutputChecksum`). At the end it looks for the CRC-32 and the
length as the eight bytes that end the file but for zero padding. Only a real trailer
matches, so appended bytes and a wrong last ISIZE are both handed to the standard library,
which raises. One `zlib.crc32` pass over the output replaces the second decode the earlier
change ruled out. That change named two costs, "the CRC-32 of the output, or a second
decode"; this is the first, the cheaper. Measured at about 0.056 s per 184 MB of output
(`zlib.crc32` at 3.3 GB/s), 6% to 15% of a full accelerated read on four cores; see
`dev-docs/formats/gzip.md`.

A candidate trailer preceded by the same CRC-32 is turned down: it is the member's ISIZE
field set to the CRC-32, with the length appended.

When a seek skipped output, there is no CRC-32 of it and no read-through is made (a
tar.gz listing would pay for the whole output); the old last-four-bytes comparison applies.

## Impact

- `compressed-streams`: the zero-padding exception is removed.
- `seekable-decompressor-streams`: the backstop finds the trailer by the CRC-32.
- `dev-docs/formats/gzip.md`, the `gzip_accel` fuzz target's exemption.
