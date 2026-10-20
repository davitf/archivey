## 1. Reader

- [x] 1.1 Frame starts become seek points on declared seek demand (`FramedDecoder` with a `StreamIndex`); skippable frames add none
- [x] 1.2 Read the seek table (`codecs/zstd_seek_table.py`) through `build_index_backwards`: bounded chunked entry reads, thinning, `SEEK_INDEX_DEGRADED` on a table that does not fit the file

## 2. Tests and docs

- [x] 2.1 `tests/test_zstd_seekable.py`: size from the table, frame resume, forward-recorded points, pzstd skippable frames, undeclared streams, degraded tables, trailing zeros, the O17 pin, thinning
- [x] 2.2 zstd handbook page, single-file page and threat-model O17 name the zstd seek table
- [x] 2.3 `openspec validate --strict zstd-seek-table`, then archive
