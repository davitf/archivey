## Why

A compressed UDIF disk image stores its blocks as zlib, bzip2 or xz. Detection stopped
at the first block, extracted it, and reported the rest of the image as trailing data.
A scan of backup disks found 138 `.dmg` files opened that way, 111 of them as a
successful 512-byte extraction.

## What Changes

- Recognise a UDIF image by its `koly` block (offset 0, or the start of the last 512
  bytes) and report `DMG` / `CERTAIN` / `magic`.
- `open_archive` raises `UnsupportedFeatureError` naming UDIF. Nothing reads the image.
- Availability is `NONE` with an empty `missing`: there is nothing to install. The
  format is known and not supported.
- A bzip2 or xz header that is the first block of the image loses to the trailer.
- The `.dmg` suffix is not a detection extension.

## Capabilities

### New Capabilities

### Modified Capabilities

- `format-detection`: a trailer step after far magic and before the content probes, the
  `koly` signature, and the refusal.
- `backend-registry`: a backend that names a format and does not read it is
  `UnsupportedFeatureError`, not a missing package.
- `detection-cost`: the one tail read is that 512-byte block. A ZIP trailer is still
  not scanned.

## Impact

- `internal/detection.py`, `detection_workspace.py`, `registry.py`, `core.py`,
  `backends/udif.py`, `types.py`.
- No new dependency. Stdlib zlib, bzip2 and xz only, so the three dependency
  configurations are not required.
- Tests: `tests/test_udif.py`.
