## 1. Recognition

- [x] 1.1 `ArchiveFormat.DMG` and a backend that declares the `koly` magic and the 512-byte trailer, with `READ_IMPLEMENTED` false
- [x] 1.2 Trailer match after far magic and before the probes; bzip2 and xz near-magic hits lose to a matching trailer
- [x] 1.3 `open_archive` raises `UnsupportedFeatureError` naming UDIF, before the registry

## 2. Contract

- [x] 2.1 Specs, handbook and user docs name the image and the pipe limit
- [x] 2.2 `tests/test_udif.py` covers the zlib, bzip2 and xz images, a real stream, a zip named `.dmg`, `format=`, and a long pipe

## 3. Verify

- [x] 3.1 `./scripts/check.sh --fix` and `./scripts/test.sh` (not `--all-configs`: no optional library changes behaviour)
- [x] 3.2 Archive this change (`openspec archive recognize-udif-dmg --yes`)
