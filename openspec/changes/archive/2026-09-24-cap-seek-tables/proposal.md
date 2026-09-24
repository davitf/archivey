# Cap the seek table by thinning it, and never resume from a broken chain

## Why

A declared-seekable xz, lzip or `.Z` stream keeps one seek point per unit it knows
about: an xz block, an lzip member, a CLEAR code. The file chooses how many units it
has. An lzip member can be 26 bytes, so the backward trailer scan held about nine times
the file's size in memory (a 16.8 MB file peaked at 150 MB), and nothing bounded it.
Listing no longer pays this (the probe folds the walk); seeking and asking for the size
still did.

davi ruled 2026-09-24, in the project thread: "an absolute limit seems to make sense
for all cases". Asked whether the index could be thinned rather than dropped, he
proposed keeping only entries some distance apart ("any seek would have to decompress
at most X extra"). The entry count is not known up front (an lzip trailer names only
the member before it; xz streams are found one at a time), so the distance adapts.

Working out where the limit could cut surfaced an existing silent short read. A point
for an xz block resumes a chain built from the block points recorded after it, and that
chain is complete only when the whole index is. When the backward index scan fails
(trailing bytes after the last stream are enough), block points a forward read had
already recorded stayed in the table, and a seek into them read to the end of the first
stream and stopped there with no error.

## What changes

- One constant cap on a stream's seek table, `MAX_SEEK_POINTS` = 262 144 entries.
- Past the cap the table is thinned, never dropped: points are kept at least a spacing
  apart in decompressed bytes, chosen so the table falls to half the cap, and later
  points keep that spacing (doubling it if the table fills again). A seek then decodes
  at most about one spacing plus one unit further than with every point.
- The backward scans thin as they walk, so they never hold more than the cap. The last
  unit is always kept, so the size they report stays exact. The xz scan sums an index's
  records without storing them once they would not fit.
- xz blocks are never dropped one at a time: a point for an xz block resumes a chain made
  of every block point after it, and a gap would make the chain decode the next listed
  block as if it followed. So thinning first replaces every block point with its
  stream's start, which decodes forward on its own; stream starts are then thinned like
  lzip members. A single stream with more blocks than the cap keeps only its start.
- Thinning emits `SEEK_INDEX_DEGRADED` (failure type `SeekTableThinned`). Nothing that
  was readable becomes an error unless a policy escalates the diagnostic, which is why
  the cap is a structural constant and not a `ListingLimits` field.
- Block points are also replaced by stream starts when an index build fails, and when a
  thinned index joins block points a forward read recorded (the thinned index can leave
  out a stream those blocks' chain would run into).

## Impact

- `seekable-decompressor-streams`: the seek-index degradation requirement and matrix.
- Real files stay far below the cap: xz `-T0` writes 24 MiB blocks, so the cap is ~6 TiB
  of xz; ncompress checks for a CLEAR every 10 kB of input, so it is ~2.6 GB of `.Z` at the
  densest.
