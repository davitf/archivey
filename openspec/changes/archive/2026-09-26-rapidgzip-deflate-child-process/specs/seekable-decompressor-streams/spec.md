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

rapidgzip 0.16 aborts the process on a DEFLATE-family stream that ends early, so the system
SHALL run the gzip, zlib and deflate decoders in a child process and MUST NOT decode those
codecs with rapidgzip in the caller's process. A path source SHALL be opened by the child. A
stream source SHALL stay in the caller's process, which serves the child's reads, seeks and
tells, so an exception from the caller's source reaches the caller as itself. The child SHALL
be ended and reaped when the stream closes or is collected. bzip2 through
`rapidgzip.IndexedBzip2File` stays in-process.

Where no child process can be started (a frozen application, an interpreter without
`sys.executable`, a spawn or temporary file the operating system refuses, a child that cannot
import rapidgzip), `AUTO` SHALL decode with the stdlib backend, as it does when rapidgzip is
absent. `ON` in that case SHALL raise `ResourceLimitError` naming `use_rapidgzip=OFF`; it MUST
NOT decode in-process.

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
| No child can be started (frozen interpreter, refused spawn or temporary file), `AUTO` | stdlib backend |
| No child can be started, `ON` | `ResourceLimitError` at open |

### Requirement: Accelerator errors translate uniformly

The system SHALL translate corrupt/truncated input from rapidgzip-backed gzip,
bzip2, deflate, and zlib into the same `compressed-streams` errors as stdlib paths:
`CorruptionError` or `TruncatedError`, never raw third-party exceptions. This
translator SHALL account for platform-varying rapidgzip exception types/messages.

For gzip through rapidgzip, the system SHALL backstop truncation by comparing full-read
decompressed length modulo 2^32 with the gzip ISIZE trailer, for **any declared-seekable
source** — a path or a caller-owned `BinaryIO` alike — not only path sources. The ISIZE trailer
value SHALL be captured up front (when the source is first inspected for backstop eligibility),
so no per-read reopen of a path is required and a non-path source needs no seek while the
accelerator is live. Where rapidgzip reaches EOF having delivered zero bytes, the system SHALL
rewind the seekable source and re-decode through the stdlib gzip engine so recoverable prefixes
stream and truncation still raises from a read (never `close()`). A conservative multi-member
scan SHALL prevent valid concatenated gzip streams from being misreported when the trailer
records only the last member.

A **caller-owned** source driven through the accelerator SHALL NOT be closed by the accelerator
or its truncation wrapper (archivey never closes a source the caller owns); the accelerator's
close-on-finalize guard closes only archivey-owned handles. rapidgzip's `terminate()`-on-raising-
source hazard on Python file objects SHALL be contained so a source-side fault surfaces as a
translated `compressed-streams` error rather than aborting the process.

A DEFLATE-family child process that ends during a request SHALL be reported by how it ended,
and every later call on the stream SHALL raise the same error: a fault signal (or its Windows
status) is a verdict on the data — `TruncatedError` when rapidgzip's abort names the
truncation, else `CorruptionError`; SIGKILL is `ResourceLimitError`; any other end is
`ReadError`. A child that ends after the caller's source raised SHALL be `ReadError`, and the
source's exception SHALL be raised first, unchanged. An exception that rapidgzip raised in the
child SHALL reach the translator as the same built-in type, and any `RuntimeError` rapidgzip
raised SHALL translate to `CorruptionError` when no listed message says truncation.

rapidgzip does not validate zlib's Adler-32 and returns a silent short read on some
mid-stream DEFLATE truncations, and raw DEFLATE carries no checksum, so there is no
ISIZE-equivalent truncation backstop for the deflate/zlib accelerator path. A DEFLATE-family
member decoded inside a container (e.g. a ZIP member) SHALL rely on the container's own
checksum (CRC-32 via the shared verifying stage) to catch truncation/corruption. A standalone
zlib/deflate stream accelerated by rapidgzip MAY therefore miss a truncation that stdlib `zlib`
would report; this is an accepted limitation of the accelerator path (tracked with the gzip
truncation work), and corruption inside a DEFLATE block SHALL still surface as `CorruptionError`.

#### Scenario: accelerator error matrix

| Case | Expected |
| --- | --- |
| Corrupt gzip/bzip2/deflate/zlib through rapidgzip | `CorruptionError`; raw accelerator exception never escapes |
| Truncated gzip through rapidgzip from a seekable **path** | `TruncatedError` via ISIZE backstop / empty→stdlib, or `CorruptionError` from accelerator; never silent short read |
| Truncated gzip through rapidgzip from a seekable **non-path** `BinaryIO` | Same as the path case — backstop active; caller source left open afterward |
| Truncated gzip/zlib/deflate that aborts rapidgzip, any source, any access pattern | The process survives; `TruncatedError` (Linux), or `CorruptionError` where the abort does not name the truncation |
| DEFLATE-family child killed by SIGKILL / ended by SIGTERM | `ResourceLimitError` / `ReadError`; later calls raise the same |
| Caller-owned source raises while the child reads it | That exception, unchanged; a later child death is `ReadError` |
| Caller-owned source after the archivey stream closes | Still open and readable; accelerator/wrapper closed only its own view |
| Truncated standalone deflate/zlib through rapidgzip | Corruption in a block → `CorruptionError`; a clean mid-stream cut MAY return a short read undetected (no checksum backstop) |
| Truncated/corrupt container DEFLATE member (e.g. ZIP) | Container CRC mismatch → `CorruptionError`/`TruncatedError` via the verifying stage |
| Valid concatenated multi-member gzip | Decompresses fully without false truncation |
| Valid empty gzip through rapidgzip | Succeeds with zero bytes |
