# Decode 7z BCJ branch filters through liblzma, not pybcj

## Why

`pybcj` takes the decoder's stream size as a C signed `int`. Two ordinary archives —
both written by 7-Zip, both reported `Everything is Ok` by `7z t`, both read back
correctly by 7-Zip itself — are unreadable because of it.

**A member of 2 GiB or more with a BCJ filter cannot be opened at all.**
`bcj.BCJDecoder(2147483648)` raises `OverflowError: signed integer is greater than
maximum`; 2147483647 is fine. Archivey passes the folder's declared unpack size
straight in, so `reader.open(member)` raises that builtin before a byte is read —
not an `ArchiveyError`, so `except ArchiveyError` does not catch it. Measured on a
2.1 GiB payload compressed with `7z a -m0=BCJ -m1=LZMA` (93 760 017 bytes on disk).
py7zr 1.1.3 fails identically on the same file, so the ceiling is pybcj's API, not
archivey's mistake.

**A member whose length is not a whole number of IA64 blocks comes back short.**
pybcj's IA64 decoder drops the trailing partial 16-byte block: a 2911-byte member
decodes to 2896 bytes, so archivey raises `TruncatedError`. Reproduces from 21 bytes
up; no size threshold and no crafted input involved.

Neither is reachable through `BCJ` + `LZMA2`, because that combination is already
folded into a single liblzma filter chain and never reaches pybcj. Everything else —
BCJ with LZMA1, PPMd, BZip2, Deflate or Copy — stages BCJ separately and does.

Clamping or chunking the size around the first bug does not work, and fails
silently in both directions; `dev-docs/known-issues.md` carries the measurements.

## What Changes

- BCJ branch filters decode through **liblzma** for every folder shape, not just
  `BCJ` + `LZMA2`. liblzma has no 32-bit stream-size limit and returns the trailing
  partial block, and its output is byte-identical to pybcj's everywhere pybcj is
  correct (verified across all six filters).
- liblzma refuses a raw chain whose only filter is a branch filter, so a BCJ stage
  standing outside the folder's main chain frames its input as LZMA2 **uncompressed**
  chunks and runs `[<branch filter>, FILTER_LZMA2]`. That compresses nothing; the
  framing costs 3 bytes per 64 KiB.
- The staging rule itself is unchanged: LZMA1 and BCJ still never go into one
  `FORMAT_RAW` filter list, because BPO-21872 truncation is a separate defect.
- `pybcj` leaves the `[recommended]` extra, and the `PackageNotInstalledError` it
  raised for LZMA1+BCJ folders goes with it. BCJ is now core for every folder shape.

## Impact

- Affected specs: `format-7z`, `packaging-and-extras`
- Affected code: `internal/streams/decompress.py`, `internal/backends/sevenzip_pipeline.py`,
  `internal/backends/sevenzip_methods.py`
- `pip install archivey[recommended]` installs one wheel fewer. No API change; archives
  that worked keep working, byte for byte.
