# Cap the seek table, and never resume from a partial one

## Why

A declared-seekable xz, lzip or `.Z` stream keeps one seek point per unit it knows
about: an xz block, an lzip member, a CLEAR code. The file chooses how many units it
has. An lzip member can be 26 bytes, so the backward trailer scan held about nine times
the file's size in memory (a 16.8 MB file peaked at 150 MB), and nothing bounded it.
Listing no longer pays this (the probe folds the walk); seeking and asking for the size
still did.

davi ruled 2026-09-24, in the project thread: "an absolute limit seems to make sense
for all cases".

Working out where the limit could cut surfaced an existing silent short read. A point
for an xz block resumes a chain built from the block points recorded after it, and that
chain is complete only when the whole index is. When the backward index scan fails
(trailing bytes after the last stream are enough), block points a forward read had
already recorded stayed in the table, and a seek into them read to the end of the first
stream and stopped there with no error.

## What changes

- One constant cap on a stream's seek table, `MAX_SEEK_POINTS` = 262 144 entries. A
  backward scan stops as soon as it passes the cap, before storing the rest; the xz scan
  checks each index's declared record count before parsing its records.
- Passing the cap, by a scan or by points a forward read records, drops the table to its
  origin, stops indexing that stream, and emits `SEEK_INDEX_DEGRADED`. Seeks then decode
  from the start. Nothing that was readable becomes an error unless a policy escalates
  the diagnostic, which is why the cap is a structural constant and not a
  `ListingLimits` field.
- When an index build fails, points carrying resume state (xz blocks) are dropped and no
  more are recorded. Stream and member starts stay, since each decodes forward on its own.

## Impact

- `seekable-decompressor-streams`: the seek-index degradation requirement and matrix.
- Real files stay far below the cap: xz `-T0` writes 24 MiB blocks, so the cap is ~6 TiB
  of xz; ncompress checks for a CLEAR every 10 kB of input, so it is ~2.6 GB of `.Z` at the
  densest.
