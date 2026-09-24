# Short input to the native stream decoders ends in an honest error

## Why

Three native decoders treated a source that ended before its first header as a valid
empty stream or as the wrong kind of error:

- An empty `.Z` source decoded to `b""` with a clean size of 0, while one or two bytes
  raised. A 1–5 byte file read as lzip also decoded to `b""`, taking the "trailing data
  after members" branch with no member seen.
- Where they did raise, xz and lzip answered `CorruptionError` for an empty source, while
  gzip, bzip2, `.lzma`, deflate and zlib answer `TruncatedError`. The general rule in
  `compressed-streams` already says unexpected end-of-input is `TruncatedError`; nothing
  recorded the native decoders as an exception to it.
- A `.Z` source cut inside a CLEAR's realignment padding ended cleanly with a short size.
  The decoder holds the exact number of padding bytes still owed, and a compressor always
  writes that padding in full, so this cut is detectable. The spec's "zero leftover bits
  … undetectable" sentence was written about leftover bits and read as covering it.

## What changes

- xz, lzip and `.Z`: a source that ends before a first complete header is `TruncatedError`
  when what arrived is empty or a prefix of the format's magic, and `CorruptionError`
  when it cannot be the start of that format. Short trailing data after a complete lzip
  member or xz stream stays allowed, as those formats specify.
- `.Z`: a source that ends while CLEAR padding is still owed raises `TruncatedError` on the
  next read, like nonzero leftover bits. Checked against 300 intact `ncompress` outputs,
  none of which ends owing padding.

## Impact

- `compressed-streams`: the error matrix gains the short-source rows.
- `seekable-decompressor-streams`: the unix-compress truncation paragraph and matrix name
  the two new detectable cuts; the undetectable case is narrowed to what it always meant.
- Behaviour: an empty `.xz` or `.lz` now raises `TruncatedError` instead of
  `CorruptionError` (CHANGELOG).
