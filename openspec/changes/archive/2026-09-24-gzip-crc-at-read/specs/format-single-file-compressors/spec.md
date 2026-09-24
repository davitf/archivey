## MODIFIED Requirements

### Requirement: Surface stored decompressed digests without decompression

The single-file backend SHALL surface a codec's stored (or cheaply derived-from-stored)
decompressed-content digest(s) on `member.hashes` when readable without decompressing,
and SHALL omit them otherwise. This serves cheap dedupe (`VISION.md` "hashes without
decompression") and never triggers a decompression pass.

Keys and value types follow the public `HashAlgorithm` / `bytes` contract (api-coherence
hashes typing). Surfacing SHALL NOT change read behavior: a full read still verifies via
the existing path; stored/derived values are metadata only.

Whether a digest is cheaply readable SHALL depend only on the codec and **the source's
shape**, never on the caller's declared member-stream capability — the founding dedupe
caller does a plain `open_archive()` and never asks to `seek()`.

- **GZIP:** the listing SHALL NOT carry a digest, and opening SHALL NOT read the
  compressed data to look for a second member. The trailer `CRC32` covers the whole
  member only when the file holds one gzip member, and proving that at open costs a pass
  over the whole file (a scan for the three-byte member magic that also false-matches in
  large compressed data). The trailer `CRC32` SHALL be added to `member.hashes` after a
  read reaches a clean end of the source through the stdlib decoder, when the input held
  exactly one member and nothing after it (no second member, no NUL padding), so the last
  8 bytes of the source are that member's trailer. The source must be seekable/path so
  the trailer can be peeked. The rapidgzip accelerator path hides member boundaries and
  SHALL NOT add it.
- **LZIP:** on a seekable source, surface `CRC32` of the whole synthetic member from the
  lzip index. For multi-member files, the value SHALL equal
  `crc32(concat(member payloads))` derived by combining per-trailer CRC-32 values with
  each member's exact uncompressed `data_size` (combine algebra). Single-member
  degenerates to the trailer CRC.
- **Non-seekable source:** omit digests that require a trailer/index peek (no forced
  decode).
- **BZ2, XZ, ZLIB, BR, `.Z`:** no cheap whole-member stored digest — omit. (Zlib's
  RFC 1950 Adler-32 trailer is verified by the decompressor on read; it is not surfaced
  on `member.hashes` because the wrapper has no size fields for a reliable
  single-stream boundary when concat/trailing junk is possible.)

#### Scenario: stored-digest surfacing by codec

| Case | `member.hashes` |
| --- | --- |
| Single-member `.gz`, seekable/path, listed before any read | no digest key |
| Single-member `.gz`, seekable/path, after a full read on the stdlib decoder | `CRC32` present (= trailer) |
| Single-member `.gz`, after a partial read | no digest key |
| `.gz` with a second member or NUL padding after the first, after a full read | no digest key |
| Opening any `.gz` | no scan of the compressed data for a second member |
| `.gz` non-seekable | no digest key |
| Single-member `.lz`, seekable source | `CRC32` present (= trailer) |
| Multi-member `.lz`, seekable source | `CRC32` present (= combine of per-member trailers) |
| `.lz` seekable, with and without `seekable_members=True` | Same `hashes` both ways |
| `.lz` from a pipe | no digest key |
| `.bz2` / `.xz` / `.zlib` / `.br` / `.Z` | no digest key |
| Any of the above, full `read()` | verification unchanged; hashes are metadata only |
