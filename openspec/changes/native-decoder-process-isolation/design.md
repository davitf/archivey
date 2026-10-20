# Design: native decoder process isolation

The options and measurements behind this change were written up before it (maintainer
report, 2026-10-20). The maintainer chose option (b), native decoders in child
processes, with the names, the pool and the shared protocol below, and agreed that AES
stays in the parent, that a per-call time limit waits, and that the docs carry the CLI
recipe.

## Decisions

### Where the seam is

Every native decoder is reached through a small object surface: `decompress(data,
max_length)` with `eof`, `needs_input`, `unused_data` (and zlib's `unconsumed_tail`);
brotli's `process` with `is_finished()` and `can_accept_more_data()`; inflate64's
`inflate` with `eof`; pyppmd's `decode` with `unused_data` read only when asked; and
`lz4.block.decompress`, a function. The decoders in `codecs/` call those surfaces on an
object they build. So the child owns that one object and the parent keeps everything
above it: the parsers, the readers, the digests, the public objects.

Each construction site takes its library from a selector (`isolated.natives(config)`),
which returns the real modules or stand-ins with the same attributes: `decompressobj`,
`BZ2Decompressor`, `LZMADecompressor`, `ZstdDecompressor`, `LZ4FrameDecompressor`,
`Decompressor`, `Inflater`, `Ppmd7Decoder` and `Ppmd8Decoder`, and the exception types
the decoders catch (`zlib.error`, `lzma.LZMAError`, `brotli.error`, `pyppmd.PpmdError`).
A stand-in's constructor returns a proxy that forwards each call to the child with its
arguments unchanged, so per-codec quirks (zlib's `max_length=0` meaning "no limit", the
others' `-1`) stay where they are today.

### One child per open decoder, from a pool

A single child for every open decoder was rejected: when one member crashes it, every
other open stream loses its decoder state, and the crash could be blamed on the wrong
member (DR-1). It would also serialize decoding across threads. So a decoder takes a child
of its own when it is built and gives it back when it is closed or collected. Starting a
child costs 12 to 18 ms on Linux, which on a 10,000-member ZIP would be minutes, so given
back children wait in a pool (at most four idle) and the next decoder reuses one: about
30 µs per small member instead.

A child goes back to the pool only when its decoder never reported an error and never
died or was interrupted. A decoder that raised on corrupt data may have left the native
library in a state nobody has tested, so that child is ended. PPMd children are never
reused: pyppmd's known fault is memory corruption after an early end, which need not
crash at once. The stdlib, zstd, lz4, brotli and inflate64 decoders have no known fault
of that kind; a reused child that one of them corrupted silently and that then crashes
on the next member would charge the wrong member, and that residual risk is accepted.

Idle children are ended at interpreter exit (`atexit`). `os.register_at_fork` empties the
pool in a forked child, which must not talk on its parent's pipes. The leak oracle in
the tests empties the pool before it counts live children.

### The generic worker

`codecs/decoder_worker.py` runs as `python -P decoder_worker.py` and imports `struct`,
`sys` and `marshal` at the top (all built in or already loaded). A table maps a codec
name to a loader that imports the library inside the function, so a zlib child never
imports pyppmd. It never imports `archivey`, which would add about 200 ms to every start.

Frames are `<BqI>` (tag, integer argument, payload size) then the payload, in both
directions; this was already rapidgzip's frame. Parent to child:

- `OPEN`: `marshal` of `(codec, args, kwargs)`. The child replies after importing the
  codec library and again after building the decoder, so a death between the two is the
  constructor's allocation (pyppmd aborts rather than raise when it cannot allocate its
  model), as the PPMd child reported before.
- `CALL`: a `marshal`ed `(method, args, kwargs, has_data)` header and then the input
  bytes, passed as the first argument when `has_data`.
- `GET`: an attribute name; the reply carries its bytes (pyppmd's `unused_data`, which
  must be read only after `eof`).
- `CLOSE`: drop the decoder. No reply; the next message is an `OPEN`.

Each reply is `OK` with a `marshal`ed state and then the output bytes, or `ERR` with
`"<module>.<qualname>\n<errno>\n<message>"`. The state carries `eof`, `needs_input`,
brotli's two predicates, a non-bytes return value, and the lengths of `unused_data` and
`unconsumed_tail` rather than their bytes: both are suffixes of input the parent sent, so
the parent rebuilds them from its own copy and they never cross the pipe twice. Where a
length does not fit that rule, the parent asks with `GET`.

The parent rebuilds an error as the type it names when that type is in a table of known
types: builtins, `zlib.error`, `lzma.LZMAError`, zstd's and lz4's errors (archivey already
imports both), and stand-ins for the libraries that re-enable the GIL (`brotli.error`,
`pyppmd.PpmdError`), which the stand-in modules expose as their `error` attributes so the
decoders' `except` clauses still match. Any other type comes back as
`DecoderChildReportedError`, which no translator maps, so it propagates.

### rapidgzip

rapidgzip is a seekable stream with source callbacks, read-ahead threads and index
queries, not a decompressor object, so it keeps its own messages. It shares the frame,
the error payload, the exact-read helper, `disable_core_dumps` and the SIGINT handling:
the child runs `decoder_worker.py rapidgzip [bzip2]`, which loads `rapidgzip_worker.py`
by path and hands it the shared module. `rapidgzip_worker.py` imports only the standard
library basics at the top, and threading inside its functions. rapidgzip children are not
pooled: one per stream, as today.

### Start failures and NONE

Under `NATIVE_DECODERS` the caller asked for a child, so a child that cannot start raises
`ResourceLimitError` naming the setting (DR-11), at the first decoder that needs one.
Under `KNOWN_CRASHES` the existing rules stay: rapidgzip `AUTO` falls back with one
warning, `ON` and PPMd over the threshold raise.

Under `NONE` the two open points from the report are settled as the most direct reading of
"everything in the same process": PPMd decodes in-process at any size, including the
bit-3 ZIP member from a pipe that today is always a child; rapidgzip is not used, because
in-process rapidgzip needs the guard PR 661 removed (a finalizer that closes it before
exit, a trap that keeps caller exceptions out of C++); `ON` together with `NONE` is a
contradiction caught at configuration.

### What is not covered

Pure-Python hangs (the parsers are bounded by limits, not time), checksums, AES through
`cryptography`, and a native hang (no deadline per call in this version). The security
page says so, and shows the recipe for a hard limit on a whole operation: run
`archivey extract` or `archivey test` in a subprocess with a timeout.

### Costs

Measured on the prototype (Linux, 4 CPUs, Python 3.11): full reads in a child take 1.03 to
1.05 times as long for bz2, 1.07 to 1.13 for lzma, 1.2 to 1.5 for zlib and about 5 for
zstd, mostly copying output through the pipe. An idle child holds about 9 MB. Deflate64
under a read budget feeds `inflate64` 64 bytes at a time, so its budgeted reads pay a
round trip per 64 compressed bytes; `NATIVE_DECODERS` accepts that for a rare codec.

## Rejected

- All of archivey in a child (option (a)): a proxy for the whole public API, callbacks
  and live member updates included, and about 200 ms per open. The CLI recipe covers the
  hard-limit use case.
- `multiprocessing`: spawn re-imports the caller's main module and gives no stderr or
  exit status to classify a crash.
- Shared memory for output: measured with rapidgzip, it did not help enough to pay for
  itself.
