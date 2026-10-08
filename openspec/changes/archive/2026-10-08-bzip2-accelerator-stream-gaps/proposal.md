# bzip2 accelerator: read stream gaps as the standard library does

## Why

The accelerator fuzz targets found rapidgzip's bzip2 decoder skipping what it cannot read
between blocks. It finds blocks by their magic, so junk before the first stream, a
stream whose header is damaged, and a stream whose block and end-of-stream magics are
both damaged are passed over, and the output goes on with the next block it recognises:
`A + B + C` with two bytes flipped in `B` read as `A + C` with no error. The combined CRC
check does not see it. The decoder also stops at zero padding, where the standard library
reads the next stream, and leaves a cut or damaged stream after the data to a
trailing-data report, where the standard library raises. An accelerator changes speed,
not behaviour, and the maintainer's 2026-10-03 ruling applies that to cut and damaged
files: the standard library gives the verdict.

## What changes

Before a read returns, the index built so far is checked: each stream starts where the
one before it ended, after nothing but zero padding and empty streams. At the first place
that does not hold, the read stops there and the standard library takes over. At the end,
when what follows the last stream starts a stream header, the standard library takes
over at the end and gives the verdict.

## Impact

- `seekable-decompressor-streams`: two rows in the accelerator error matrix.
- `dev-docs/formats/bzip2.md`: the two sharp edges for zero padding and damaged trailing
  empty streams are gone; the cost of the takeover is the new one.
