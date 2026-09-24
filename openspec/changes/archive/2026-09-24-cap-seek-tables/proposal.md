# Cap the seek table by thinning it, with xz points that resume on their own

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

He then asked whether resuming inside an xz stream intrinsically needs every block's
bounds. It does not. The old resume wrapped each block in its own synthetic stream,
whose synthetic index had to state that block's sizes, so a resume was a chain built
from every block point after it. liblzma decodes the real bytes from any block to the
end of the stream's blocks behind one synthetic stream header, still checking each
block (checked on liblzma 5.4.5). Needing the chain was an artifact of the wrapping.

The chain also hid an existing silent short read. It was complete only when the whole
index was: when the backward index scan failed (trailing bytes after the last stream
are enough), a seek into block points a forward read had recorded read to the end of
their stream and stopped there with no error.

## What changes

- xz resume becomes `_XzBlockResume`: a synthetic stream header, then the real bytes
  from the resume block to where the stream's index begins. Each block point carries its
  stream's check type, where its blocks end, and its decompressed and compressed ends.
  The decoded size must match the index's size for the rest of the stream, since the
  index itself is not fed; then decoding continues sequentially past the stream. The
  block chain, and the requirement that the table hold every block after a resume point,
  go away, and with them the silent short read.
- One constant cap on a stream's seek table, `MAX_SEEK_POINTS` = 262 144 entries.
- Past the cap the table is thinned, never dropped: points are kept at least a spacing
  apart in decompressed bytes, chosen so the table falls to half the cap, and later
  points keep that spacing (doubling it if the table fills again). A seek then decodes
  at most about one spacing plus one unit further than with every point.
- The backward scans thin as they walk, so they never hold more than the cap. The xz
  scan thins a stream's blocks as it walks that stream's index, so a stream declaring
  millions of blocks is never held whole. The last unit is always kept, so the size the
  scans report stays exact.
- Thinning emits `SEEK_INDEX_DEGRADED` (failure type `SeekTableThinned`). Nothing that
  was readable becomes an error unless a policy escalates the diagnostic, which is why
  the cap is a structural constant and not a `ListingLimits` field.

## Impact

- `seekable-decompressor-streams`: the seek-index degradation requirement and matrix.
- A resumed xz read no longer cross-checks each block's sizes against the index, only
  the rest of the stream's total; a read from the start still checks the whole index.
- Real files stay far below the cap: xz `-T0` writes 24 MiB blocks, so the cap is ~6 TiB
  of xz; ncompress checks for a CLEAR every 10 kB of input, so it is ~2.6 GB of `.Z` at the
  densest.
