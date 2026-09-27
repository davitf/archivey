# Bound the zstd window with the decoder memory cap

## Why

A zstd frame declares its window, and libzstd refused any window over its own default
of 128 MiB with a `ZstdError` that archivey reported as `CorruptionError`. The limit was
not the caller's, so `DecoderLimits.max_decoder_memory` could not lift it, and a
`zstd --long=31` stream written from standard input (a 2 GiB window) could not be read.
The `compressed-streams` spec pinned every `ZstdError` to `CorruptionError`.

## What changes

The zstd decoder's `window_log_max` comes from `DecoderLimits.max_decoder_memory`,
rounded down to a power of two and clamped to libzstd's bounds. A window over the cap
raises `ResourceLimitError`, as a liblzma dictionary over the cap does. A window over
libzstd's own ceiling (2^31 on a 64-bit build, which is also the default cap) raises
`UnsupportedFeatureError`, because no cap lifts it. Detection probes keep decoding
uncapped, as they already do for liblzma.

## Impact

- `compressed-streams`: the error-translation requirement carves the two window
  refusals out of `CorruptionError`, and the matrix gains two rows.
- Code: `ZstdCodec` in `internal/streams/codecs.py`.
