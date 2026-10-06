# Refuse an lzip member in another version as unsupported

## Why

An lzip member whose header has the `LZIP` magic and a version byte other than 1 raised
`CorruptionError` on the forward read, and the backward trailer walk did not check the
version at all. A version-0 file (lzip before 1.0) is a valid file that archivey does
not read, not damage, and the exception type is what a caller branches on. The walk
gap was worse: a version-0 member after a version-1 one was looked past as trailing
data, so the reported size left it out and a seek past it served the next member's
bytes. The maintainer ruled for `UnsupportedFeatureError` on 2026-10-03.

## What changes

The forward decoder and the backward walk refuse any version other than 1 with
`UnsupportedFeatureError`. A full `LZIP` magic always starts a member, after a
version-1 member as at the start of the file, as `lzip` itself treats it. The walk
checks the version at each member header it reaches; a seek that cannot rely on the
walk falls back to the sequential read, which refuses the member, so a seek never skips
one.

## Impact

- `format-single-file-compressors`: a new requirement, "An lzip member in another
  version is unsupported".
- Code: `internal/streams/lzip.py`.
- Docs: `docs/formats.md`, `dev-docs/formats/xz.md`.
