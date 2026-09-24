# Tasks — cap the seek table

- [x] 1.1 `MAX_SEEK_POINTS`, `SeekIndexTooLarge` and `check_seek_index_size` in
      `decompressor_stream.py`; the lzip and xz backward scans stop at the cap.
- [x] 1.2 `DecompressorStream` drops the table to its origin and stops indexing when a
      scan or recorded points pass the cap, with `SEEK_INDEX_DEGRADED`.
- [x] 1.3 After a failed index build, drop stateful points and refuse new ones.
- [x] 2.1 Tests with the cap monkeypatched small: lzip and xz scans, forward reads, block
      points recorded before the cap, and the failed-scan short read.
- [x] 2.2 CHANGELOG. Archive this change.
