# Seekable Decompressor Streams

## Purpose

Archivey provides random access inside single-file compressed streams when the
caller declares seekability. The subsystem uses native indexes where formats
provide them and optional rapidgzip accelerators where they do not, while keeping
forward-only streams free of seek machinery.

## Related specs

| Spec | Relationship |
| --- | --- |
| `compressed-streams` | Public `open_stream(..., seekable=...)` and codec backend dispatch |
| `archive-reading` | `seekable_members=True` on archive member streams |
| `access-mode-and-cost` | Declared capabilities vs access modes |
| `diagnostics` | Rewind and seek-index diagnostic policy/retention |
| `error-handling` | Codec exception translation and `DiagnosticRaisedError` |
## Requirements
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

### Requirement: XZ and lzip use format-native indexes

The system SHALL support random access in XZ and lzip by reading their embedded
index structures. XZ SHALL use the footer and block index to map uncompressed
offsets to compressed block positions. Lzip SHALL scan trailers to locate block
boundaries. A seek SHALL decompress only the block range needed for the requested
uncompressed offset.

#### Scenario: native index matrix

| Case | Expected |
| --- | --- |
| Seekable XZ source opens | Footer/block index maps uncompressed offsets to compressed positions |
| Seek within XZ | Only containing block range is decompressed |
| Seekable lzip source opens | Trailer scan locates block boundaries |
| Seek within lzip | Only required block range is decompressed |

### Requirement: Gzip and bzip2 random access use rapidgzip only

The system SHALL use `rapidgzip` as the only optional accelerator library for
both gzip and bzip2: gzip through `rapidgzip.RapidgzipFile`, bzip2 through
`rapidgzip.IndexedBzip2File`. It MUST NOT import the standalone `indexed_bzip2`
package because loading both C++ cores in one process can corrupt the heap on
macOS. `use_indexed_bzip2` remains the bzip2 configuration flag but selects the
rapidgzip-bundled decoder.

When rapidgzip is unavailable or disabled, gzip and bzip2 SHALL use stdlib
decoders; backward seek is serviced by re-decompressing from the start and MUST
not degrade silently.

#### Scenario: accelerator matrix

| Case | Expected |
| --- | --- |
| `use_rapidgzip` enabled and package installed | gzip seeks without full re-decompression |
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
| Caller-owned source after the archivey stream closes | Still open and readable; accelerator/wrapper closed only its own view |
| Truncated standalone deflate/zlib through rapidgzip | Corruption in a block → `CorruptionError`; a clean mid-stream cut MAY return a short read undetected (no checksum backstop) |
| Truncated/corrupt container DEFLATE member (e.g. ZIP) | Container CRC mismatch → `CorruptionError`/`TruncatedError` via the verifying stage |
| Valid concatenated multi-member gzip | Decompresses fully without false truncation |
| Valid empty gzip through rapidgzip | Succeeds with zero bytes |

### Requirement: Accelerator lifecycle is safe at shutdown

The system SHALL protect rapidgzip streams with a `weakref.finalize` guard that
closes the raw object exactly once when the wrapper is collected or at
interpreter exit. The guard MUST hold a strong reference to the raw object until
close completes, because `join_threads()` alone does not stop rapidgzip's C++
worker thread and an unclosed worker can abort the process during finalization.

The system SHALL keep one accelerator library in process by using rapidgzip for
both gzip and bzip2 and never importing standalone `indexed_bzip2`. With these
measures, `AUTO` MAY select rapidgzip for declared random access on every
platform.

#### Scenario: accelerator safety matrix

| Case | Expected |
| --- | --- |
| Accelerator stream is leaked, cyclically collected, or process exits | Finalizer closes raw object before free; process terminates cleanly |
| Process accelerates gzip and bzip2 | Both use rapidgzip; standalone `indexed_bzip2` is absent; no cross-library heap double-free |
| Declared random access with `AUTO` and rapidgzip available | rapidgzip may be selected on every platform |

### Requirement: Index-less rewinds emit diagnostic data

The predicate for `STREAM_REWIND_REDECOMPRESSES` SHALL be **the decoded progress the
rewind discards**, not the codec's identity:

```
redecode_distance = position_before_seek − nearest_seek_point_at_or_before(target)
```

That is, the bytes that must be decoded again to return to where the caller was. It is
measured from the **pre-seek position**, not from the target: the seek call itself may
decode almost nothing — `seek(10)` after reading a gigabyte decodes ten bytes — while
throwing away a gigabyte of progress, and it is the discarded gigabyte that makes a seek
loop quadratic.

When a backward seek's `redecode_distance` reaches
`REWIND_REDECODE_WARN_BYTES` (an **absolute** byte threshold), the system SHALL report
`STREAM_REWIND_REDECOMPRESSES` with codec, before/after offsets, the distance, and
accelerator name or `None`. Forward and no-op seeks SHALL report nothing.

The threshold SHALL be absolute rather than relative to the jump distance. A relative
rule goes quietest exactly where the absolute cost is highest: on a 1 GB single-block
`.xz`, seeking from the end back to 900 MB re-decodes 900 MB while jumping only ~100 MB,
a ratio no sane relative threshold would flag.

The distance SHALL be computed against the seek index **as it exists at seek time**, and
obtaining it MUST NOT trigger index construction — a diagnostic that built an index would
change the cost it reports.

**The predicate is uniform across codecs, including accelerated ones.** A format that
*can* carry an index does not always *have* a useful one, and neither does an
accelerator: a single-block `.xz` has one seek point (the origin), and `rapidgzip`'s
`available_block_offsets()` on a `gzip.compress` output is sparse enough that a backward
seek can re-decode megabytes with the accelerator engaged. Both are the same event and
SHALL be reported the same way.

| Stream shape | Nearest resume point | Reported |
| --- | --- | --- |
| No index at all | the origin | yes, once progress passes the threshold |
| Single-block xz / one-member lzip / `.Z` with no CLEAR codes | the origin | yes, same as above |
| Multi-block xz, multi-member lzip | the containing block | only when the progress since that block passes the threshold |
| Accelerated gzip/bzip2, dense index | the containing block | as above |
| Accelerated gzip/bzip2, sparse index | up to a block back | yes, once past the threshold |
| Any codec, less progress discarded than the threshold | — | no |
| `STORED` (no decompression) | — | never; nothing is re-decoded |

A stream that exposes no seek-point table SHALL be treated as resuming from the origin.
For a *decompressing* stream — the only kind this event applies to — that is the truth
rather than a guess, and it is what keeps the index-less codecs (stdlib LZMA Alone,
brotli, lz4) reporting.

The event SHALL live on the stream operation and cumulative owning-reader
aggregate, never on `CostReceipt` or `ArchiveInfo`. Gzip/bzip2/deflate/zlib context names the
`[seekable]` accelerator when a rapidgzip path was eligible; brotli, lz4, and zstd record no
accelerator. Stdlib zstd SHALL rewind in place like other index-less codecs. When a
deflate/zlib stream falls back to stdlib `zlib` (accelerator absent, `OFF`, or below the
`AUTO` threshold), its rewind SHALL name the `[seekable]` accelerator, consistent with the
gzip fallback.

**Recording is deduplicated; escalation is not.** The occurrence SHALL be *recorded* at
most once per stream, and the configured `DiagnosticPolicy` SHALL be evaluated on **every**
qualifying seek, so a `RAISE` policy stops the second expensive rewind as well as the
first. See `diagnostics` for the general rule.

#### Scenario: slow rewind matrix

| Case | Expected |
| --- | --- |
| One index-less stream performs many backward seeks | One recorded `STREAM_REWIND_REDECOMPRESSES`; later rewinds add no duplicate record |
| Rewind diagnostic resolves to `RAISE`, first qualifying seek | `DiagnosticRaisedError` raised from that seek |
| Rewind diagnostic resolves to `RAISE`, **second** qualifying seek | `DiagnosticRaisedError` raised again — the guard does not disarm |
| Only forward/no-op seeks occur | No occurrence |
| Full rewind on a **single-block** `.xz` | One occurrence — the format could have carried an index; this file does not |
| Rewind within one block of a multi-block `.xz` | No occurrence |
| Rewind of fewer than `REWIND_REDECODE_WARN_BYTES` on any codec | No occurrence |
| Rewind across a sparse gap in an engaged `rapidgzip` index | One occurrence naming the accelerator |
| zstd stream rewinds via stdlib backend | Re-decompresses from start in place; one occurrence when above the threshold |
| Stdlib-fallback zlib/deflate stream rewinds | One occurrence naming the `[seekable]` accelerator |
| Stream with no seek-point table (stdlib LZMA Alone, brotli, lz4) | Resume point is the origin; one occurrence once progress passes the threshold |
| `STORED` member seeks backward | No occurrence, at any distance |

### Requirement: Recoverable seek-index degradation is diagnostic data

When an XZ/lzip backward index or trailer scan fails but sequential
decompression remains safe, the system SHALL emit `SEEK_INDEX_DEGRADED` with
codec, scan kind, and public failure type, then use sequential fallback unless
policy escalates. The occurrence SHALL be aggregate-only on stream/reader
operation summaries. Unsafe corruption SHALL remain a typed
`CorruptionError`/`TruncatedError`, not a recoverable diagnostic.

#### Scenario: seek-index degradation matrix

| Case | Expected |
| --- | --- |
| Recoverable XZ index scan failure | `SEEK_INDEX_DEGRADED` collected/logged; stream falls back sequentially |
| Same issue resolves to `RAISE` | `DiagnosticRaisedError`; no fallback |
| Corruption prevents correct sequential decoding | `CorruptionError` / `TruncatedError`, not diagnostic fallback |

### Requirement: Unix-compress uses CLEAR seek points

When seekability is declared and the compressed source is seekable, the system
SHALL register `SeekPoint`s at stream start (after the 3-byte `.Z` header) and at
each LZW CLEAR realignment. A seek SHALL resume from the nearest preceding
seek point with an empty dictionary and MUST NOT emit
`STREAM_REWIND_REDECOMPRESSES`.

Forward decode SHALL NOT call `seek` on the compressed source: CLEAR bit-block
realignment MUST use a bounded in-memory buffer. When seekability is not
declared, the system SHALL NOT retain a CLEAR seek-point table. When the source
is not seekable, the decompressor stream SHALL report `seekable() is False` and
`seek` SHALL raise `io.UnsupportedOperation`.

Unix-compress has no length or checksum trailer. At source EOF, after all
decoded bytes have been delivered, the system SHALL best-effort detect
truncation: if any leftover bits remain after the last complete LZW code and
those bits are nonzero (finished compressors zero-pad), the next `read()` SHALL
raise `TruncatedError`. Zero leftover bits (including a cut exactly on a code
boundary) SHALL end successfully — such truncation remains undetectable.

Unknown reserved header flag bits (`0x60` in the third header byte) SHALL raise
`UnsupportedFeatureError` when the header is parsed.

#### Scenario: unix-compress seek matrix

| Case | Expected |
| --- | --- |
| Seekable `.Z`, `seekable=True`, seek backward across a CLEAR | Resumes from CLEAR/`SeekPoint`; no rewind diagnostic |
| Seekable `.Z`, `seekable=False` | Forward-only; no CLEAR table retained |
| Non-seekable `.Z` pipe, forward read | Decompresses; `seekable()` false |
| Truncated `.Z` with nonzero leftover bits | Yields available bytes; next `read()` raises `TruncatedError` |
| Truncated `.Z` with only zero leftover bits | Yields fewer bytes; no `TruncatedError` (undetectable) |
| Header flag byte has reserved bits `0x60` set | `UnsupportedFeatureError` |
| Corrupt LZW codes | `CorruptionError` |

