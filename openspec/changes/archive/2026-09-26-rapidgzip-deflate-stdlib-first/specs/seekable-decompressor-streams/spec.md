## MODIFIED Requirements

### Requirement: Gzip and bzip2 random access use rapidgzip only

The system SHALL use `rapidgzip` as the only optional accelerator library for
both gzip and bzip2: gzip through `rapidgzip.RapidgzipFile`, bzip2 through
`rapidgzip.IndexedBzip2File`. It MUST NOT import the standalone `indexed_bzip2`
package because loading both C++ cores in one process can corrupt the heap on
macOS. `use_indexed_bzip2` remains the bzip2 configuration flag but selects the
rapidgzip-bundled decoder.

A gzip stream that selects rapidgzip SHALL follow the stdlib-first rule of the
DEFLATE-family requirement: rapidgzip decodes only input that the stdlib engine has
decoded to a clean end.

When rapidgzip is unavailable or disabled, gzip and bzip2 SHALL use stdlib
decoders; backward seek is serviced by re-decompressing from the start and MUST
not degrade silently.

#### Scenario: accelerator matrix

| Case | Expected |
| --- | --- |
| `use_rapidgzip` enabled and package installed | gzip reads through stdlib until the first backward seek; after it, seeks without full re-decompression |
| `use_indexed_bzip2` enabled and rapidgzip installed | bzip2 uses `rapidgzip.IndexedBzip2File`; standalone `indexed_bzip2` never imports |
| rapidgzip absent or flag `OFF` | stdlib decoder; backward seek re-decompresses and warns |

### Requirement: DEFLATE-family random access uses rapidgzip

The system SHALL use `rapidgzip` (the `[seekable]` accelerator, `>=0.16.0`) as the optional
random-access/parallel backend for raw DEFLATE (`deflate` codec) and zlib-wrapped DEFLATE
(`zlib` codec), in addition to gzip. rapidgzip auto-detects `GZIP`/`ZLIB`/`DEFLATE`, so the
codec SHALL pass the stream through unwrapped — no synthetic gzip header/footer. Selection is
gated identically to gzip (`use_rapidgzip` × declared seekability × availability, plus the
`AUTO` minimum-input-size threshold) and the raw accelerator SHALL be wrapped in the same
close-on-finalize guard. The default sequential backend is unchanged: when rapidgzip is
unavailable, `OFF`, or below the `AUTO` threshold, deflate/zlib decode through stdlib `zlib`.

rapidgzip 0.16 aborts the process (`std::terminate` from a destructor) when it decodes a
gzip, zlib or raw DEFLATE stream that ends early, and no source wrapper or `try/except` can
contain it. When a seekable gzip, zlib or deflate stream selects rapidgzip, the system SHALL
therefore decode reads and forward seeks with the stdlib engine, and SHALL switch the stream
to rapidgzip at the first backward seek only when the stdlib engine has decoded the whole
input to a clean end: during the caller's reads, at a seek to the end, or in a separate
stdlib pass that the backward seek runs. When that pass fails, the stream SHALL stay on the
stdlib engine, so the caller meets the failure on a read. An `ArchiveyError` that the pass
saw SHALL be raised again at the clean end of the stdlib stream, for a source stage that
reports its verdict only once. A non-seekable source goes to rapidgzip directly, which
refuses it before it decodes.

rapidgzip over-reads past a DEFLATE end-of-stream looking for a concatenated member, so the
codec SHALL feed it an exactly-bounded input (e.g. the container's `SlicingStream` sized to
the member's compressed length); an unbounded or over-long stream MAY raise a spurious
"Invalid deflate block" error on the trailing bytes.

#### Scenario: deflate/zlib accelerator matrix

| Case | Expected |
| --- | --- |
| Declared-seekable raw deflate, `use_rapidgzip` enabled, size ≥ threshold | stdlib until the first backward seek; then rapidgzip seeks without full re-decompression; input passed unwrapped |
| Declared-seekable zlib stream, accelerator enabled | Same; rapidgzip auto-detects ZLIB after the switch |
| Sequential read to the end, no backward seek | rapidgzip never opens |
| Backward seek on a truncated gzip/zlib/deflate stream | Stays on stdlib; a read raises `TruncatedError`; the process never aborts |
| rapidgzip absent, `OFF`, or size < `AUTO` threshold | stdlib `zlib` (`-15` / `MAX_WBITS`); backward seek re-decompresses from start |
| Accelerator fed an over-long/unbounded slice | May raise a spurious decode error on trailing bytes; callers MUST bound the input |

### Requirement: Accelerator errors translate uniformly

The system SHALL translate corrupt/truncated input from rapidgzip-backed gzip,
bzip2, deflate, and zlib into the same `compressed-streams` errors as stdlib paths:
`CorruptionError` or `TruncatedError`, never raw third-party exceptions. This
translator SHALL account for platform-varying rapidgzip exception types/messages.
Every rapidgzip `RuntimeError` that no listed message classifies SHALL translate to
`CorruptionError`. An exception that the caller's own source raised, and that the source
trap re-raised, SHALL reach the caller untranslated. No truncated or corrupt input SHALL
abort the process.

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

rapidgzip does not validate zlib's Adler-32, and raw DEFLATE carries no checksum. Truncation
of a standalone zlib or deflate stream SHALL be detected by the stdlib-first rule: rapidgzip
decodes only input that stdlib `zlib` has decoded to a clean end. A DEFLATE-family member
decoded inside a container (e.g. a ZIP member) SHALL also rely on the container's own
checksum (CRC-32 via the shared verifying stage) to catch truncation/corruption.

#### Scenario: accelerator error matrix

| Case | Expected |
| --- | --- |
| Corrupt gzip/bzip2/deflate/zlib through rapidgzip | `CorruptionError`; raw accelerator exception never escapes |
| rapidgzip `RuntimeError` with an unlisted message | `CorruptionError` |
| `RuntimeError` raised by the caller's own source | Reaches the caller unchanged |
| Truncated gzip through rapidgzip from a seekable **path** | `TruncatedError` via the stdlib-first rule, ISIZE backstop or empty→stdlib; never silent short read; never a process abort |
| Truncated gzip through rapidgzip from a seekable **non-path** `BinaryIO` | Same as the path case — backstop active; caller source left open afterward |
| Caller-owned source after the archivey stream closes | Still open and readable; accelerator/wrapper closed only its own view |
| Truncated standalone deflate/zlib with the accelerator selected | `TruncatedError` from the stdlib engine; never a process abort |
| Truncated/corrupt container DEFLATE member (e.g. ZIP) | Container CRC mismatch → `CorruptionError`/`TruncatedError` via the verifying stage |
| Valid concatenated multi-member gzip | Decompresses fully without false truncation |
| Valid empty gzip through rapidgzip | Succeeds with zero bytes |
