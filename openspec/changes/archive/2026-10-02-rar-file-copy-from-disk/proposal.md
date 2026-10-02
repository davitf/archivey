# Write RAR5 file copies from the extracted source; opt out of copy streams

## Why

Since `rar-file-copy-solid-pass`, a solid pass keeps each file-copy source until the
pass ends, in memory up to 8 MiB and otherwise in a temporary file charged to
`SpoolLimits.max_bytes`. Extraction does not need that copy for a source it has just
written: the same bytes are already on disk. A caller that only records hashes does not
need the copy's bytes at all, since a copy's bytes and digests are its source's.

## What changes

- Extraction tells the pass not to keep a source it is writing from the pass's stream,
  and writes each later copy of it by copying the written file. The file must still be
  the one written (device, inode, size and modification time, checked on the opened
  file). Otherwise the copy is read from the pass, which keeps the sources extraction
  did not write and decodes again the ones it did. A dry run keeps sources as before.
- `stream_members(file_copy_streams=False)` yields `None` for every file copy, and a
  solid pass then keeps nothing for copies. The default is unchanged.

Yielding each copy right after its source, so a pass holds one source at a time, is not
part of this change: it changes the yield order.

## Impact

- `archive-reading`: `stream_members()` gains the keyword-only `file_copy_streams`.
- `format-rar`: "Serve a solid pass's file copies from the source it decoded" says what
  extraction and `file_copy_streams=False` do, with matrix rows.
- Code: `file_copy_pass.py` (new), `base_reader.py`, `reader.py`, `extraction.py`,
  `rar_reader.py`, `rar_copy_sources.py`; `_iter_with_data` takes the pass's
  `FileCopyPass` in every backend that overrides it.
