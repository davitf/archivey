# Tasks — cap the seek table

- [x] 1.1 `_XzBlockResume` replaces the block chain; block points carry their stream's
      check type, blocks end and ends; `_XzState(after_stream=True)` continues past it.
- [x] 1.2 `MAX_SEEK_POINTS`, `SpacedCollector`, `spaced_subset` and `thinning_spacing` in
      `decompressor_stream.py`; the lzip and xz backward scans thin as they walk and
      report it, and `build_index_backwards` emits the diagnostic.
- [x] 1.3 `DecompressorStream` thins a table past the cap and keeps the spacing after.
- [x] 2.1 Tests with the cap monkeypatched small: the collector, lzip and xz scans,
      forward reads, a resume after a thinned index, a stream over the cap on the
      progressive scan, the resume's size check and truncation, and the failed-scan
      short read.
- [x] 2.2 CHANGELOG. Archive this change.
