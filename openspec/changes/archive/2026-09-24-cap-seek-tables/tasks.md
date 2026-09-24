# Tasks — cap the seek table

- [x] 1.1 `MAX_SEEK_POINTS`, `SpacedCollector`, `spaced_subset` and `thinning_spacing` in
      `decompressor_stream.py`; the lzip and xz backward scans thin as they walk and
      report it, and `build_index_backwards` emits the diagnostic.
- [x] 1.2 `DecompressorStream` thins a table past the cap and keeps the spacing after.
- [x] 1.3 Block points become their stream's start (`_XzBlockBounds.stateless_point`)
      when the table is thinned, when an index build fails, and when a thinned index
      joins recorded block points.
- [x] 2.1 Tests with the cap monkeypatched small: the collector, lzip and xz scans,
      forward reads, block points recorded before a thinned index, a stream over the
      cap on the progressive scan, and the failed-scan short read.
- [x] 2.2 CHANGELOG. Archive this change.
