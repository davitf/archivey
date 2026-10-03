## MODIFIED Requirements

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
and every later call on the child stream SHALL raise the same error: a fault signal (or its
Windows status) is a verdict on the data — `TruncatedError` when rapidgzip's abort names the
truncation, else `CorruptionError`; SIGKILL is `ResourceLimitError`; any other end is
`ReadError`.

A fault signal, like a data error rapidgzip reports, SHALL hand the read to the standard
library decoder, which delivers what it would have delivered with the accelerator off: the
same bytes before the error, and the same error. rapidgzip decodes ahead of the reader, so
when it dies the reader can be far short of the fault. The standard library SHALL start at
the last DEFLATE block boundary from rapidgzip's index that the delivered output passed, with
the 32 KiB of output before it as its window, where the stream has seen one; otherwise at the
start of the stream. A decode started at such a point that reaches the end of its DEFLATE
stream cannot check the CRC-32 or Adler-32 that follows, so it SHALL start over from the start
of the stream. A SIGKILL or other end that is not a fault signal is not a verdict on the data
and SHALL reach the caller as above. A child that ends after the caller's source raised SHALL be `ReadError`, and the
source's exception SHALL be raised first, unchanged. An exception that rapidgzip raised in the
child SHALL reach the translator as the same built-in type, and any `RuntimeError` rapidgzip
raised SHALL translate to `CorruptionError` when no listed message says truncation.

rapidgzip does not validate zlib's Adler-32 and returns a silent short read on some
mid-stream DEFLATE truncations. For a zlib stream the system SHALL check the Adler-32 after
rapidgzip: once the stream has been read to its end (a forward seek reads the skipped bytes, so
a reader that skips is still covered), a mismatch the standard library confirms SHALL raise
`CorruptionError` from the read or seek that reached the end, and the TAR end-of-archive scan
SHALL re-raise it. Raw DEFLATE carries no checksum, so there is no backstop for the deflate
accelerator path. A DEFLATE-family member decoded inside a container (e.g. a ZIP member) SHALL
rely on the container's own checksum (CRC-32 via the shared verifying stage) to catch
truncation/corruption. A standalone deflate stream accelerated by rapidgzip MAY therefore miss a
truncation that stdlib `zlib` would report; this is an accepted limitation of the accelerator
path, and corruption inside a DEFLATE block SHALL still surface as `CorruptionError`.

#### Scenario: accelerator error matrix

| Case | Expected |
| --- | --- |
| Corrupt gzip/bzip2/deflate/zlib through rapidgzip | `CorruptionError`; raw accelerator exception never escapes |
| Truncated gzip through rapidgzip from a seekable **path** | `TruncatedError` via ISIZE backstop / empty→stdlib, or `CorruptionError` from accelerator; never silent short read |
| Truncated gzip through rapidgzip from a seekable **non-path** `BinaryIO` | Same as the path case — backstop active; caller source left open afterward |
| Truncated gzip/zlib/deflate that aborts rapidgzip, any source, any access pattern | The process survives; the bytes and the error of the standard library decoder (`TruncatedError`) |
| A fault signal ends the child on a valid stream | The standard library reads on from the delivered position; the caller loses nothing |
| A resumed standard-library decode reaches the end of a DEFLATE stream (a later member, bytes after the data) | It starts over from the start of the stream; the stream's checksum is checked |
| DEFLATE-family child killed by SIGKILL / ended by SIGTERM | `ResourceLimitError` / `ReadError`; later calls raise the same |
| Caller-owned source raises while the child reads it | That exception, unchanged; a later child death is `ReadError` |
| Caller-owned source after the archivey stream closes | Still open and readable; accelerator/wrapper closed only its own view |
| Truncated or damaged standalone zlib through rapidgzip, read to its end | `CorruptionError` or `TruncatedError`; never a silent short or wrong read (Adler-32 check) |
| Damaged `.tar.zz` through rapidgzip, listed or read | `CorruptionError` (the TAR end scan re-raises the Adler-32 mismatch) |
| Truncated standalone deflate through rapidgzip | Corruption in a block → `CorruptionError`; a clean mid-stream cut MAY return a short read undetected (no checksum backstop) |
| Truncated/corrupt container DEFLATE member (e.g. ZIP) | Container CRC mismatch → `CorruptionError`/`TruncatedError` via the verifying stage |
| Valid concatenated multi-member gzip | Decompresses fully without false truncation |
| Valid empty gzip through rapidgzip | Succeeds with zero bytes |
