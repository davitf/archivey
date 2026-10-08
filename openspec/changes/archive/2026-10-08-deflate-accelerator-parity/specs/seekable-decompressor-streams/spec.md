## MODIFIED Requirements

### Requirement: DEFLATE-family random access uses rapidgzip

The system SHALL use `rapidgzip` (the `[seekable]` accelerator, `>=0.16.0`) as the optional
random-access/parallel backend for raw DEFLATE (`deflate` codec) and zlib-wrapped DEFLATE
(`zlib` codec), in addition to gzip. rapidgzip auto-detects `GZIP`/`ZLIB`/`DEFLATE`, so the
codec SHALL pass the stream through unwrapped — no synthetic gzip header/footer. Selection is
gated identically to gzip (`use_rapidgzip` × declared seekability × availability, plus the
`AUTO` minimum-input-size threshold). The default sequential backend is unchanged: when
rapidgzip is unavailable, `OFF`, or below the `AUTO` threshold, deflate/zlib decode through
stdlib `zlib`.

Because rapidgzip auto-detects the format, the codec SHALL decode with the stdlib backend a
source rapidgzip could take for another format: a `deflate` source that starts with `1f 8b`,
a valid zlib header or `BZh` and a digit from 1 to 9, and a `zlib` source that does not start
with a valid zlib header. A `zlib` source whose header sets a preset dictionary SHALL use the
stdlib backend too: rapidgzip does not take that header for zlib. A raw DEFLATE stream that
rapidgzip ends before its first byte of output SHALL be decoded by the stdlib backend, which
gives the verdict. Once the stdlib backend has taken over from rapidgzip, its errors SHALL
leave as the codec's typed errors, so the over-run probe of a declared size never takes a
data error for the end of the data.

rapidgzip 0.16 aborts the process on a DEFLATE-family stream that ends early, so the system
SHALL run the gzip, zlib and deflate decoders in a child process and MUST NOT decode those
codecs with rapidgzip in the caller's process. A path source SHALL be opened by the child. A
stream source SHALL stay in the caller's process, which serves the child's reads, seeks and
tells, so an exception from the caller's source reaches the caller as itself. The child SHALL
be ended and reaped when the stream closes or is collected. bzip2 through
`rapidgzip.IndexedBzip2File` stays in-process.

Where no child process can be started (a frozen application, an interpreter without
`sys.executable`, an archivey imported from a zip with no worker script on disk, a spawn or
temporary file the operating system refuses, a child that cannot import rapidgzip), `AUTO`
SHALL decode with the stdlib backend, as it does when rapidgzip is absent, and SHALL log one
warning per process on the `archivey.streams` logger naming the reason and
`use_rapidgzip=OFF` (none where rapidgzip is absent). `ON` in that case SHALL raise
`ResourceLimitError` naming the reason and `use_rapidgzip=OFF`; it MUST NOT decode
in-process.

rapidgzip over-reads past a DEFLATE end-of-stream looking for a concatenated member, so the
codec SHALL feed it an exactly-bounded input (e.g. the container's `SlicingStream` sized to
the member's compressed length); an unbounded or over-long stream MAY raise a spurious
"Invalid deflate block" error on the trailing bytes.

#### Scenario: deflate/zlib accelerator matrix

| Case | Expected |
| --- | --- |
| Declared-seekable raw deflate, `use_rapidgzip` enabled, size ≥ threshold | rapidgzip decodes/seeks without full re-decompression; input passed unwrapped |
| Declared-seekable zlib stream, accelerator enabled | rapidgzip auto-detects ZLIB and decodes; backward seek without re-decompress from start |
| rapidgzip absent, `OFF`, or size < `AUTO` threshold | stdlib `zlib` (`-15` / `MAX_WBITS`); backward seek re-decompresses from start |
| Accelerator fed an over-long/unbounded slice | May raise a spurious decode error on trailing bytes; callers MUST bound the input |
| No child can be started (frozen interpreter, zip import, refused spawn or temporary file), `AUTO` | stdlib backend |
| No child can be started, `ON` | `ResourceLimitError` at open |
| A `deflate` source that starts like gzip, zlib or bzip2, or a `zlib` source with no valid zlib header or with a preset dictionary, `ON` | The bytes and error of `OFF` (`CorruptionError` at the header) |
| A raw DEFLATE stream cut before any output (`03`), `ON`, with or without a declared size | `TruncatedError`, as with `OFF` |
| A `deflate` or `zlib` stream declared empty that does not decode, `ON` | `CorruptionError`, as with `OFF` |
