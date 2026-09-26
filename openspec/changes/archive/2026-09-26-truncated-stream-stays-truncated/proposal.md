# A truncated decompressing stream stays truncated

## Why

`DecompressorStream` raised a deferred truncation error once and then cleared it. The
next read returned `b""` with no error, so a truncated stream read as a short, clean one.
When the failed read was a whole-stream `read()`, a `seek(0)` did not help either, and a
second `read()` published the truncated prefix as the stream's size.

## What Changes

- The stream records the deferred error it raised. Later reads with no buffered bytes,
  and `seek(0, SEEK_END)`, raise it again, and no size is published.
- Any seek restarts the decoder from the nearest seek point, so the decode reaches the
  same error again at the same place.
- The decoder-composition matrix stops saying forward-only codecs keep `pending_error`
  `None`: zlib, brotli, PPMd and deflate64 set it when their input ends incompletely.

## Impact

- Code: `internal/streams/decompressor_stream.py`.
- Tests: `tests/test_codecs.py`.
- Not changed: the `[seekable]` rapidgzip accelerator path, a separate stream.
