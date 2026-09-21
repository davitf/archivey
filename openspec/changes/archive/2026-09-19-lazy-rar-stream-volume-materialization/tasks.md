# Tasks — lazy RAR stream-volume materialization

## 1. Per-volume ranges

- [x] 1.1 Add `ConcatenatedFile.volume_ranges`, returning `(start, size)` per volume in
      the concatenated byte space, so a format opener can read one volume at a time
      through whatever it already holds over the concatenation.

## 2. The reader

- [x] 2.1 Hold the stream volumes as `_stream_volume_items` instead of materializing
      them in `_open_shared_source`.
- [x] 2.2 Add `_volume_set_size()` — volumes in this set whether or not they are files
      yet — and use it where `len(self._volume_paths)` stood for "is this a volume set":
      the SFX origin hand-off, the incomplete-set check, `is_multivolume`, and
      `rar.volume_count`.
- [x] 2.3 Parse stream volumes from one `SharedSource` view per volume, seeking view 0
      to `_volume0_parse_origin` for an SFX first volume.
- [x] 2.4 Materialize from `_ensure_archive_path`, writing the whole set.
- [x] 2.5 Copy stream items through a shared view rather than reading the item directly,
      so the copy takes the same lock every other read of these volumes takes. `Path`
      items keep `shutil.copy2`: they are not shared state.
- [x] 2.6 Make the stream-volume `cost.notes` caveat predictive.

## 3. Pins

- [x] 3.1 `test_stream_volume_listing_writes_nothing` — listing creates no temp
      directory.
- [x] 3.2 `test_stream_volume_read_materializes_once` — the first read writes the whole
      set once, a second read reuses it, and close removes it.
- [x] 3.3 Update `_stream_volumes_copy_note_present` to the predictive wording.
