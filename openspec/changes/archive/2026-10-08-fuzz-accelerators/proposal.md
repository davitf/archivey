# Fuzz the seekable accelerators

## Why

The Atheris harness forced the `[seekable]` accelerators off, because rapidgzip and its
bzip2 decoder are native code that can loop where no Python alarm reaches. So the
accelerators sat outside the fuzzed surface, and the user docs had to say so, while
`AcceleratorMode.AUTO` engages them by default when the extra is installed.

## What changes

Four Atheris targets (`gzip_accel`, `zlib_accel`, `deflate_accel`, `bzip2_accel`)
decode each input twice, with the accelerator forced on and forced off, and fail when:

- a byte the accelerated stream returns, on the first read or after a seek, differs
  from the decode with the accelerator off at the same offset;
- the accelerated read ends cleanly where the decode with it off raised.

zlib and raw DEFLATE inputs carry a four-byte declared decompressed size, since `AUTO`
engages rapidgzip on them only with a container-declared size. These targets do not use
the Python per-input alarm: libFuzzer's own `-timeout` and `-rss_limit_mb` bound them,
which fire while the main thread is inside native code.

## Impact

- `testing-contract`: "Coverage-guided fuzz gate for parsers and entry points" no
  longer forces accelerators off everywhere and gains the accelerator targets.
- `tests/atheris_fuzz/`, `.github/workflows/atheris-fuzz.yml`, and the docs that said
  the accelerators were not fuzzed.
