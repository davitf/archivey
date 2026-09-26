# Tasks — short input to the native stream decoders

## 1. Decoders

- [x] 1.1 xz and lzip: a source ending before a first header raises `TruncatedError` for
      empty or magic-prefix input, `CorruptionError` otherwise.
- [x] 1.2 `.Z`: the same split for the 3-byte header, and `TruncatedError` for a source
      ending inside CLEAR padding.

## 2. Proof and documents

- [x] 2.1 Tests for each codec's truncated and corrupt short inputs, and the hand-built
      `.Z` cut inside CLEAR padding.
- [x] 2.2 CHANGELOG.
- [x] 2.3 Archive this change.
