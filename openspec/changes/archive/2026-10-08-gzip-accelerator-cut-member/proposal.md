# gzip accelerator: a cut member before a complete one raises

## Why

The accelerator fuzz targets found a silent short read. When a gzip member is cut short
and a complete member follows it, rapidgzip stops at the cut and ends softly. The ISIZE
backstop then sees a length mismatch, finds the further member (it is real, and zlib
confirms it), and stands down, because the last trailer only records the last member. The
caller gets the first member's prefix and no error; the standard library raises. The same
path let a crafted last trailer that matches the bytes delivered before the cut pass.

## What changes

The rapidgzip child reports its compressed position. Before the backstop compares lengths
or stands down for a further member, it checks that the decode reached the end of the
source. A decode that stopped short hands the read to the standard library, which gives
its verdict.

## Impact

- `seekable-decompressor-streams`: the backstop paragraph and one row in the accelerator
  error matrix.
- `dev-docs/formats/gzip.md`: the backstop description and the ISIZE-trust sharp edge.
