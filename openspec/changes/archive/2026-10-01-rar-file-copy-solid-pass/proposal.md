# Serve a solid pass's RAR5 file copies from the source it decoded

## Why

A RAR5 file copy (`rar -oi`) has no data of its own. `unrar p` and `unar` emit nothing
for it, so a solid pass served each copy from a named open of its source, which decodes
the solid stream again from its start. Fifty copies of a 1 KiB file behind 100 MiB of
solid data decoded about 5 GiB in 51 decompressor runs, at about 60 header bytes per copy,
and no limit tripped.

## What changes

A solid pass keeps each source a later copy reads as its pipe passes it and serves the
copies from that: one decompressor run per pass. Up to 8 MiB per pass stays in memory;
past that, a source goes to one temporary file charged to `SpoolLimits.max_bytes`, from
its first decoded byte until the pass ends. A source with no room is not kept, and its
copies fall back to the named open. `SpoolLimits.max_bytes` stays the one limit on
temporary storage and now also bounds these kept sources.

## Impact

- `archive-reading`: "Bounded implicit temporary storage" and "Explicit configuration
  object" say that `spool_limits` also bounds kept decoded data, and that a keep over it
  is declined, not refused.
- `format-rar`: one added requirement with its matrix.
- Code: `rar_copy_sources.py` (new), `rar_reader.py`, `spool.py`
  (`SpoolBudget.try_reserve`, `SpoolBudget.release`), `config.py` (docstring).
