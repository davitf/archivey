# Take a cut stream over from rapidgzip at its last index point

## Why

rapidgzip decodes ahead of the reader in worker threads, and 0.16 aborts the child
process when one of them decodes the chunk that holds the cut of a truncated stream.
Everything it had decoded past the reader dies with it. Measured on 4 cores, a 154 MB
gzip cut at 10 % delivered nothing before the error, where the standard library delivered
20 MB, and the loss grows with the thread count. The spec only promised that the process
survives and the error is typed. Turning parallelism off would give up the speed, and
decoding the whole stream again with the standard library would cost a full pass on a
large file.

## What changes

A child crash hands the read to the standard library, as a data error rapidgzip reports
already did. The standard library starts at the last DEFLATE block boundary from
rapidgzip's index that the reader passed, with the 32 KiB of output before it as zlib's
window, so the second decode costs the distance from that point to the cut. Python's
`zlib` cannot start mid-byte, so the input starts with empty DEFLATE blocks whose length
ends at the block's bit offset. A resumed decode that reaches the end of its DEFLATE
stream starts over from the start, because it cannot check the stream's checksum.

## Impact

- `seekable-decompressor-streams`: "Accelerator errors translate uniformly" gains the
  takeover and its checkpoint, and the matrix gains two rows.
- Code: `internal/streams/deflate_resume.py` (new), `rapidgzip_child.py` and
  `rapidgzip_worker.py` (checkpoints, a `POINTS` request), `decompress.py` (the zlib and
  gzip decoders resume at a checkpoint), `codecs.py` (`_StdlibOnAcceleratorError` takes
  over on a crash and starts at the checkpoint; the declared-size gzip path gets it too).
