# Cap the LZMA dictionary size an archive declares

## Why

`DecoderLimits.max_decoder_memory` bounds the working memory a codec allocates because the
archive's own header asked for it (`archive-reading`). It shipped covering PPMd only. Every
LZMA container carries the same kind of number — the dictionary size — in a header the
archive writes: 7z coder properties, the ZIP method-14 header, each xz block header, the
`.lzma` header and each lzip member header.

liblzma reserves the whole declared dictionary when the decoder is built and touches it as
output is written, so the declaration bounds how much of the output stays resident.
Measured on liblzma 5.4.5: a 151 KB stream declaring 4 GiB held 1.1 GiB resident after
producing 1 GiB of zeros; the same stream declaring 1 MiB held 59 MB.

Capping it surfaced one error-reporting case. The Alone content probe claims bytes that are
not LZMA at all — `tests/test_probe_provenance_unconfirmed.py` uses an OLE/CFB header over
zero padding — and reading that file now reads four arbitrary header bytes as a 2.7 GiB
dictionary and refuses with `ResourceLimitError`. `error-handling` stamps a probe-only read
failure as `format_unconfirmed`, but only for `TruncatedError` and `CorruptionError`, so
the refusal would tell the caller to raise a limit for a file that was never `.lzma`.

## What changes

- The LZMA dictionary is checked against `max_decoder_memory` before a decoder is built on
  every path that declares one. xz passes the cap to liblzma as `memlimit`, which is checked
  after each block header and before allocating for it, with a 128 KiB allowance for the
  decoder's own overhead.
- Detection probes (the codec content probes and the inner-TAR probe) decode uncapped: their
  output is bounded, and a capped probe would misidentify a large-dictionary stream for a
  caller who opened it with `DecoderLimits.UNLIMITED`.
- `error-handling`: a `ResourceLimitError` raised while reading a probe-only member is
  stamped like a truncation or corruption. No new type.
- `.lzma` refuses on the first read rather than on open, as xz and lzip already do. The
  single-file reader opens a codec stream eagerly inside `open_archive`, before the
  probe provenance is attached to the reader, so a refusal raised on open could never be
  stamped. No decoder is built either way.

## Impact

`archive-reading`'s `decoder_limits` requirement already says what the cap is for; this
change implements it for LZMA and needs no delta there. The `error-handling` delta widens
which exception types the probe-provenance stamp applies to.
