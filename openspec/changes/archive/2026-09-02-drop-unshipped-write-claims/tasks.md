# Tasks

- [x] 1. Drop `format-zip`'s streaming-write requirement and set its write-support row to No.
- [x] 2. Set `format-single-file-compressors`' write-support row to No (same false claim, one cell).
- [x] 3. Fix `format-zip`'s Purpose prose, which also asserts streaming write. Purpose is not a
      requirement, so it is edited directly rather than through a delta.
- [x] 4. `openspec archive drop-unshipped-write-claims --yes` and commit the `openspec/specs/` diff.
