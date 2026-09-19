## MODIFIED Requirements

### Requirement: Seek machinery is demand-driven

The system SHALL construct seek support only when seekability is declared:
`seekable_members=True` on `open_archive()` or `seekable=True` on the
single-stream API. Undeclared streams SHALL NOT parse XZ footers, scan lzip
trailers, instantiate rapidgzip accelerators, retain rewind buffers, or retain
seek-point tables; they are forward-only.

`use_rapidgzip` and `use_indexed_bzip2` SHALL resolve their `AUTO` / `ON` / `OFF`
configuration against declared seek demand, not the `streaming` access-mode
proxy. For declared-seekable streams, native XZ/lzip indexes, rapidgzip-backed
gzip/bzip2 indexes, and stdlib fallback rewinds retain their contracts. The
stdlib O(n)-per-rewind path MAY serve the seek but MUST emit the documented
slow-rewind diagnostic/warning.

Under `AUTO`, the system SHALL additionally require the known compressed input size to reach a
documented minimum threshold before selecting rapidgzip for any DEFLATE-family stream (deflate,
zlib, gzip), so per-stream accelerator setup is not paid for members too small to benefit. The
threshold value is fixed by benchmark and recorded in design. When the input size is not known
in advance, `AUTO` SHALL behave as it did before this threshold existed (select the accelerator
when otherwise eligible). `ON` ignores the threshold; `OFF` never selects rapidgzip.

#### Scenario: demand matrix

| Case | Expected |
| --- | --- |
| gzip/xz/bzip2/lzip opened without seekability under `AUTO` | No index, no accelerator, forward-only |
| Same stream opened with seekability, `AUTO`, accelerator installed, size ≥ threshold | Accelerator or native index provides random access |
| Declared-seekable deflate/zlib/gzip under `AUTO`, known size < threshold | rapidgzip not selected; stdlib backend used |
| Declared-seekable DEFLATE-family stream, `use_rapidgzip=ON`, size below threshold | rapidgzip still selected (threshold ignored) |
| Declared-seekable gzip without accelerator, caller seeks backward | Seek re-decompresses from start and warns/names `[seekable]` accelerator |
