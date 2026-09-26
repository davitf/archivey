# rapidgzip: decode the DEFLATE family in a child process

## Why

rapidgzip 0.16 aborts the whole process (`std::terminate` from a throwing destructor,
`BitReader::tell()` "The bit buffer should not contain more data than have been read from
the file!") when it decodes a gzip, zlib or raw DEFLATE stream that ends early. It happens
for a path, a file object and a `BytesIO` alike. On an 8 MB gzip, 27 of 30 random cuts
aborted on Linux. `seekable_members=True` on a truncated `.gz` of 1 MiB or more, or a cut
ZIP deflate member read with the accelerator on, kills the caller's interpreter. No Python
`try/except` can catch it. bzip2 (`IndexedBzip2File`) did not abort in 110 tries.

## What Changes

- gzip, zlib and deflate decode through rapidgzip in a child Python process that runs a
  standalone worker script (`rapidgzip_worker.py`), as PPMd does. The parent keeps the
  seekable stream the codec layer wraps (`RapidgzipChildStream`). A path source is opened by
  the child; a stream source is read by the parent on the child's behalf.
- A child that dies is reported by how it died: an abort that names the truncation is
  `TruncatedError`, another crash `CorruptionError`, SIGKILL `ResourceLimitError`, anything
  else `ReadError`. Every later call raises the same error.
- Where no child can run, `AUTO` uses the stdlib backend when that is known up front (a frozen
  application); otherwise the open raises `ResourceLimitError` naming `use_rapidgzip=OFF`.
- bzip2 stays in-process.

## Impact

- Public: no new API. `use_rapidgzip=OFF` is the way to avoid the child process.
- Cost: about 25 ms to start the child plus one round trip per request (see design).
- Code: `internal/streams/codecs.py`; new `rapidgzip_child.py`, `rapidgzip_worker.py`,
  `child_exit.py`.
- Tests: `tests/test_accelerator_truncation_abort.py`; `scripts/bench_rapidgzip_child.py`.
