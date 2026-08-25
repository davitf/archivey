# Compressed Streams

## Purpose

Compressed streams are the shared pull-stream layer that turns compressed or
encrypted bytes into decompressed bytes. Format parsers compose this layer rather
than calling codec libraries directly, so codecs, AES decryption, exception
translation, dependency checks, digest verification, diagnostics, and compressed
byte accounting are implemented once.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-data-model` | `CompressionMethod`, member hashes, and standalone raw-stream formats |
| `seekable-decompressor-streams` | Seekable/indexed behavior when seekability is requested |
| `error-handling` | Typed exception hierarchy and cause preservation |
| `diagnostics` | Digest, rewind, and seek-index diagnostic policy/retention |
| `backend-registry` | Codec availability and install hints for format support |
## Requirements

### Requirement: Format parsers use the shared decompressor-stream layer

The system SHALL expose codec decompression through one pull-based
`open_stream(...)`-style API returning `BinaryIO`/`ArchiveStream`. Single-file
compressors, native 7z, and future native ZIP SHALL compose shared stream
backends and MUST NOT directly import or drive codec libraries such as `pyppmd`,
`inflate64`, raw `lzma` filters, or the crypto backend.

#### Scenario: shared pipeline matrix

| Case | Expected |
| --- | --- |
| Native 7z decodes Delta + LZMA2 | Builds pipeline from shared stream backends |
| 7z and future ZIP need Deflate64 | Both use the same `inflate64`-backed stream backend |

### Requirement: open_stream is forward-only unless seekability is requested

The single-stream API SHALL default to a forward-only stream and accept
`seekable: bool = False`. Without `seekable=True`, the stream reports
`seekable() is False`, `seek()` raises `io.UnsupportedOperation`, `tell()` works,
and no seek index or accelerator is instantiated. With `seekable=True`, the
`seekable-decompressor-streams` contract SHALL apply. Concurrency is not a
parameter because the API returns one stream.

#### Scenario: seekability matrix

| Case | Expected |
| --- | --- |
| Open compressed source without `seekable=True` | Reads forward; `seekable()` false; `seek()` unsupported; no index |
| Open same source with `seekable=True` | Seekable behavior follows `seekable-decompressor-streams` |

### Requirement: One StreamCodec descriptor describes each codec

The system SHALL register each single-stream codec through one descriptor
containing its open function, exception translator, exact magic signatures,
optional content probe, file extensions, metadata extractor, and optional
dependency requirement (package/extra/tool, install hint, unlocked capability).
A codec SHALL be recognized by exact magic or content probe; there is no separate
weak-magic flag. Descriptor construction MUST NOT eagerly import optional codec
libraries.

A content probe SHALL receive the peeked prefix and MAY additionally receive the
**length of the source** when the caller knows it. The length is an optional input,
not a required one: a probe that does not need it SHALL be unaffected, and a probe that
uses it SHALL behave as before when it is absent — an unknown length means "cannot apply
the check", never "reject".

Two uses follow from those two inputs together, and neither requires a new one:

- **Framing.** A probe MAY test a declared framing length against the bytes the source can
  actually hold (see `format-detection`). Brotli tests a declared meta-block length against
  the source. **LZMA Alone** tests the weaker version of the same invariant: its 13-byte
  header is followed by range-coder payload, so a source no longer than the header cannot
  be an Alone stream — which was the whole of its measured real-world false-positive set,
  4 files in 40 000, each exactly 13 bytes.
- **Completeness.** When `source_length` does not exceed the prefix the probe was handed,
  the probe holds the whole source, and a decode that still wants more input after a
  **declared bounded output drain** SHALL be a rejection (see `format-detection`). This is
  available to every probe without any interface change, and it is why the sentence above
  no longer names zlib as a probe that has no use for the length: completeness applies to
  every probe that decodes.

A probe MUST NOT use the source length to read beyond the prefix it was given, **except**
through a bounded read facility the caller supplies explicitly for that purpose. Where such
a facility exists, it SHALL be optional, absent by default, and bounded in both offset
range (forward-only ceiling today: 1 MiB) and number of links walked; a probe that does not
take it SHALL behave exactly as it does today. This exception exists for the self-describing
block chain in `format-detection`, whose successor offsets frequently sit past a 4 KiB
prefix, and it does not license open-ended reading.

Registering a standalone codec descriptor SHALL make detection, the single-file
reader, and availability reporting work without edits elsewhere.

#### Scenario: descriptor matrix

| Case | Expected |
| --- | --- |
| New standalone codec descriptor is registered | `detect_format()`, `SingleFileBackend`, and availability reporting pick it up |
| Import `archivey` with no optional codec packages | No third-party codec import and no `ImportError` |
| Probe that ignores the source length | Same verdict with the length supplied or omitted |
| Probe that uses it, length known | May reject a prefix whose declared framing exceeds the source |
| Probe that uses it, length unknown (non-seekable source longer than the peek) | Falls back to the prefix-only verdict; MUST NOT reject on that basis |
| LZMA Alone probe, known length ≤ 13 | Reject — a source that is only the header carries no range-coder payload |
| LZMA Alone probe, length unknown | Today's prefix-only verdict |
| Any probe, `source_length <= len(prefix)`, decode wants more input within the output drain | Reject — the whole source is visible and the stream does not terminate |
| Any probe, `source_length <= len(prefix)`, decode completes within the output drain | Accept |
| Probe offered no bounded read facility | Behaves exactly as today; prefix is its whole world |
| Probe given one, reads past the prefix within its bound | Permitted, for the block-chain walk only |
| Probe given one, attempts an unbounded or unlimited-count read | Not permitted |

### Requirement: Each supported codec has a default backend

The system SHALL decompress supported codecs through these default backends:

| Codec | Default backend | Availability |
| --- | --- | --- |
| gzip | stdlib `gzip` | core |
| bzip2 | stdlib `bz2` | core |
| xz | native xz stream over stdlib `lzma` | core |
| LZMA Alone | stdlib `lzma` `FORMAT_ALONE` | core |
| LZMA1 / LZMA2 raw | stdlib `lzma` `FORMAT_RAW` | core |
| Delta, BCJ x86/ARM/ARMT/PPC/SPARC/IA64 | `lzma` raw filters | core |
| raw Deflate | stdlib `zlib` (`-15`) | core |
| Copy/STORED | pass-through | core |
| zstd | stdlib `compression.zstd` (3.14+) / `backports.zstd` (<3.14) | optional `[recommended]` before 3.14; core on 3.14+ |
| lz4 | `lz4` | optional `[recommended]` |
| Brotli | `brotli` | optional `[recommended]` |
| unix-compress `.Z` | native LZW `DecompressorStream` | core |
| PPMd var.H | `pyppmd` | optional `[recommended]` |
| Deflate64 | `inflate64` | optional `[recommended]` |
| AES-256 decrypt stage | wrapped crypto backend | optional `[recommended]` |

LZMA Alone SHALL be a distinct stream-codec descriptor from raw LZMA1/LZMA2
(`FORMAT_RAW` + properties). Alone is standalone (`StreamFormat.LZMA_ALONE`);
raw LZMA1/LZMA2 remain container-only.

#### Scenario: backend matrix

| Case | Expected |
| --- | --- |
| Default gzip stream | stdlib `gzip` |
| Default zstd on Python 3.14+ | stdlib `compression.zstd` |
| Default zstd on Python 3.11-3.13 with `backports.zstd` | `backports.zstd` using the same API |
| Standalone `.lzma` / Alone stream | `lzma` in `FORMAT_ALONE` mode |
| 7z folder LZMA2 raw stream | `lzma` in `FORMAT_RAW` mode |
| Default unix-compress `.Z` stream | native LZW stream; no `uncompresspy` import |
| Core-only install opens `.Z` | Succeeds without optional extras |

### Requirement: AES decryption is one wrapped pipeline stage

The system SHALL use `cryptography` from `[recommended]` through an internal wrapper
only. AES decryption SHALL be a stream stage composed before decompression, such
as AES then LZMA2 for an encrypted 7z folder. Format parsers MUST use the wrapper
instead of importing `cryptography` directly.

#### Scenario: crypto matrix

| Case | Expected |
| --- | --- |
| AES-encrypted 7z folder over LZMA2 with `cryptography` installed | Pipeline applies AES decrypt stage, then LZMA2 |
| Any format parser needs AES | Uses internal crypto abstraction |

### Requirement: Missing optional backends raise PackageNotInstalledError

The system SHALL raise `PackageNotInstalledError`, naming the missing package,
extra, or tool, when the selected codec/decrypt backend requires an unavailable
optional component.

#### Scenario: missing backend matrix

| Case | Expected |
| --- | --- |
| PPMd stream without `pyppmd` | `PackageNotInstalledError` naming `pyppmd` |
| AES stream without `cryptography` | `PackageNotInstalledError` naming the crypto backend |

### Requirement: Returned streams translate decompression errors

The system SHALL wrap backend streams so decompression failures surface as
Archivey exceptions: corrupt data as `CorruptionError`, unexpected end-of-input
as `TruncatedError`, and source seek requirements as the documented non-seekable
error. No raw backend exception SHALL escape. For zstd specifically,
`compression.zstd.ZstdError` SHALL map to `CorruptionError`, and its truncation
`EOFError` SHALL map to `TruncatedError`.

#### Scenario: decompression error matrix

| Case | Expected |
| --- | --- |
| Corrupt compressed stream is read | `CorruptionError` with backend exception as `__cause__` |
| Compressed stream ends mid-data | `TruncatedError` |
| Zstd stream ends before end-of-frame marker | `TruncatedError`, not a silent short read |
| Zstd checksum frame is corrupted | `CorruptionError` with backend `ZstdError` as `__cause__` |

### Requirement: Content faults raise from read, never from close

This requirement scopes to the streams this layer owns: `DecompressorStream`
(and every codec `Decoder` behind it) and `VerifyingStream` / the fused
`MemberVerifier`. Other backends (the rapidgzip accelerator and its
`_GzipTruncationCheckStream`, and any third-party wrapper) are **out of scope**
here; they already surface content faults from `read` rather than `close`, and
retargeting them is deferred (see the rapidgzip follow-up). The wording below is
a standing rule for the in-scope streams, not a claim that every stream type in
the library has been audited to it.

Decode and verify streams SHALL raise content `TruncatedError` and
`CorruptionError` from `read` / `readall` (and from size/seek paths that would
otherwise report a false clean completion). `close()` MUST NOT raise those
content faults. `close()` MAY still propagate teardown failures (`OSError`,
translated inner-close errors).

Public bounded `read(n)` for `n ≥ 1` on `ArchiveStream` and
`VerifyingStream` / `MemberVerifier` SHALL be **full-count**: return exactly `n`
bytes unless a terminal boundary is reached (clean EOF, truncation-shaped short,
or a raised content error). Implementations SHALL coalesce over short-reading
inners (e.g. via `streamtools.read_full_count` (stop on short, not RawIOBase `read_exact`)). `read(0)` is a no-op, never EOF.

`VerifyingStream` / fused `MemberVerifier` SHALL verify digests (CRC and other
expected hashes) when a read **reaches the member's end**:

- **Size-declared** (`expected_size` set): the read that consumes the declared
  size is a verifying event (checksum and over-run). On digest mismatch or
  over-run it SHALL raise `CorruptionError` and return **no bytes** for that call
  (withhold the final chunk). On truncation-shaped EOF before the declared size,
  the first read that asks past available output returns the remaining prefix
  (short return); the next empty `read` raises `TruncatedError`.
- **Size-unknown**: every data chunk MAY be returned first; `CorruptionError`
  SHALL raise on the read that observes end-of-stream (typically the terminal
  empty `read`) — no mandatory one-chunk delayed-release lookahead.

On `readall` / `read(-1)`, the complete-stream read SHALL include the EOF verdict
and SHALL raise `CorruptionError` on mismatch (and `TruncatedError` on hash-less
short) so `read(); close()` cannot silently accept bad content. `finish_on_close`
SHALL close the inner and MUST NOT introduce a first content `TruncatedError` /
`CorruptionError` solely because the caller is closing.

A seek off the sequential frontier SHALL forfeit digest verification for the rest
of the handle's life. Length / truncation / over-run checks SHALL remain active and
SHALL key off bytes actually read (not a seek-updated logical position alone). When a
seek jumps the logical position to/past the declared size without reading the
intervening bytes, concluding SHALL read that skipped gap (bounded by the declared
size) **and probe one byte past the declared size**, reproducing the same length +
over-run verdict a sequential reaching read runs, rather than returning `b""` blind.
So a past-EOF `seek(declared_size)` on a **truncated** member MUST NOT silence
`TruncatedError`, and on an **over-long** member (one that decodes past its declared
size) MUST NOT silence `CorruptionError`. Symmetrically, the same jump on a
**complete** member MUST NOT fabricate either fault: a seek to/past the declared size
followed by `read` returns `b""` (standard `BinaryIO` past-EOF semantics), and the
`seek(member.size); read(1)` completeness idiom works.
A member already read to its declared size is length-verified, so a later seek past
the end concludes with no extra reads.

Deliberate partial read then close before clean EOF remains quiet for
digest/length verification (abandon before verdict), modulo the length checks
that only fire when a read reaches EOF / asks past available.

On the complete-stream (`readall` / `read(-1)`) path the verifier SHALL drain
the inner to genuine EOF (`inner.read` returning `b""`) in **bounded** steps.
It MUST NOT assume a single `inner.read` returns the whole body: `inner` is an
arbitrary `BinaryIO` and MAY return fewer bytes than requested without being at
EOF (a short read). A single `inner.read(remaining)` therefore under-returns on
any short-reading inner and skips the EOF verdict — the drain loop fixes both.
When a decompressed size is declared, each step SHALL stay capped by the
remaining declared byte count so a corrupt/adversarial **over-long** stream is
stopped at the declared size (raising `CorruptionError`) and never slurped
unbounded into memory. This is why the sized path MUST NOT delegate to
`inner.read(-1)`; the size cap is a decompression-bomb bound, and the code
carrying it SHALL say so inline. The unsized path (no declared size, no cap)
MAY delegate to `inner.read(-1)` and then run the EOF verdict.

#### Scenario: close vs read matrix

| Case | Expected |
| --- | --- |
| Truncated `DecompressorStream`; catch on empty `read`; then `close()` | `close()` succeeds |
| Truncated gzip stdlib path; error already observed on `read`; then `close()` | `close()` succeeds |
| Size-declared digest/CRC mismatch; `read(expected_size)` or chunk reaching size | Raises `CorruptionError`; that call returns no bytes (withholds) |
| Size-unknown digest/CRC mismatch; chunked `read(n)` | All content bytes delivered; terminal empty `read` raises `CorruptionError`; `close()` quiet |
| Digest/CRC mismatch; `read()` / `read(-1)` | Raises `CorruptionError` (complete-stream verdict); `close()` alone does not raise the digest fault |
| `read(); close()` with bad CRC | `read()` raises — must not succeed quietly |
| Hash-less short member; `read(-1)` | Raises `TruncatedError` |
| Hash-less short; chunked until empty | Available prefix delivered; terminal empty `read` raises `TruncatedError` |
| Exact-available `read(k)` then `close` (k == decompressed length < declared) | Quiet — did not ask past available |
| `read(-1)` over a short-reading inner (returns `< n`, not EOF) | Full body gathered via bounded drain; EOF verdict fires in that call |
| Bounded `read(n)` over a short-reading inner | Full-count: returns `n` or short only at terminal boundary |
| `read(-1)` over an over-long inner with a declared size | Stopped at the declared size; `CorruptionError`; inner not read unbounded past the cap |
| Seek off frontier then short of declared size | Checksum forfeited; `TruncatedError` still raises on completing/empty read |
| Seek to/past declared size on a **complete** member, then `read` (incl. `seek(size); read(1)`) | Returns `b""`; no fabricated `TruncatedError` (checksum forfeited by the seek) |
| Seek to/past declared size on a **truncated** member, then `read` | Concluding reads the skipped gap; `TruncatedError` with the true recoverable length |
| Seek to/past declared size on an **over-long** member, then `read` | Concluding reads the gap and probes past the declared size; `CorruptionError` (over-run), not a silent `b""` |
| Partial read then `close` before clean EOF (verify) | No digest/length verdict |
| Inner teardown fails on `close` | Teardown error may propagate |

### Requirement: Decompressed output digests are verified at clean EOF

The verification stage SHALL compute available expected digest algorithms
incrementally over decompressed bytes and raise `CorruptionError` for a
computable mismatch at clean EOF. A mismatch SHALL surface from the terminal read
after all data chunks have been delivered; a bytes-returning full read raises and
returns no bytes. Partial/random-access reads SHALL NOT produce a digest verdict.

Supported computable algorithms SHALL include `crc32` (via `zlib.crc32`),
`adler32` (via `zlib.adler32`), the `hashlib.algorithms_available` set, and
`blake2sp` (the 8-way parallel BLAKE2s tree hash used by RAR5), computed via an
internal zero-dependency hasher. A well-formed member carrying only a `blake2sp`
digest SHALL therefore be verified, not skipped. When an expected `adler32` is
installed on a verifying stream, it SHALL likewise be computed and checked (not
skipped as unknown).

When an expected digest cannot be computed because the algorithm is genuinely unknown
or a backend is missing, the system SHALL emit `DIGEST_UNVERIFIABLE` with algorithm,
non-secret reason, and member identity when available. Diagnostic policy controls
collection, logging/callback delivery, member attachment, and escalation.

#### Scenario: digest matrix

| Case | Expected |
| --- | --- |
| Expected `blake2sp` on a well-formed RAR5 member | Computed and verified; mismatch raises `CorruptionError` |
| Expected `adler32` on a verifying stream | Computed and verified; mismatch raises `CorruptionError` |
| Expected digest under a genuinely-unknown algorithm name | `DIGEST_UNVERIFIABLE` counted/retained/logged; bytes still returned without that check |
| Full member read reaches EOF with computable digest mismatch | `CorruptionError` naming the algorithm |
| Chunked read reaches EOF with mismatch | All valid chunks delivered; following terminal read raises |
| Caller abandons stream before clean EOF | No digest verdict or mismatch exception |
| Unverifiable digest resolves to `RAISE` | `DiagnosticRaisedError` halts open/read |

### Requirement: Public ArchiveStream exposes bounded operation diagnostics

Every public `ArchiveStream` SHALL expose an immutable `diagnostics` snapshot. A
reader-owned stream shows an operation-filtered view over the reader collector; a
standalone codec stream owns a stream-lifetime collector. Serving the view SHALL
not retain another aggregate copy of each occurrence.

#### Scenario: ArchiveStream diagnostics matrix

| Case | Expected |
| --- | --- |
| Standalone codec stream emits index/rewind diagnostic | `stream.diagnostics` exposes exact counts and bounded details without a reader |
| Reader-owned member stream emits diagnostic | Stream view and reader aggregate share one retained occurrence |

### Requirement: Read-only stream wrappers share one internal base

Read-only wrappers in this layer SHALL share an internal base for the read-only
`BinaryIO` surface (`readable`, `writable`, `write`) and canonical `readinto` /
`readall` built from each wrapper's `read`. The public codec-stream path SHALL
return an `ArchiveStream` carrying stream-level presentation metadata; internal
`backend.open()` calls MAY return raw backend streams.

The seekable decompressor path SHALL be a single concrete stream class
(`DecompressorStream`) parameterized by a `Decoder` strategy, not a per-codec
subclass hierarchy. Every codec — forward-only and segmented alike — SHALL plug in
through **one** decoder protocol, which also owns seek-index discovery:

```python
@dataclass
class DecodeOut:
    data: bytes
    points: list[SeekPoint]  # absolute; empty for forward-only codecs

class Decoder(Protocol):
    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Decoder: ...
    def feed(self, chunk: bytes) -> DecodeOut: ...
    def flush(self) -> DecodeOut: ...
    @property
    def finished(self) -> bool: ...
    @property
    def pending_error(self) -> BaseException | None: ...
    def clear_pending_error(self) -> None: ...
    # Default no-op; only index-bearing codecs (xz, lzip, future BGZF) override it.
    def build_index(
        self, inner: BinaryIO, last_known: SeekPoint
    ) -> tuple[list[SeekPoint], int | None]: ...
```

The stream — not the decoder — SHALL own the buffer, position, seek-point table,
and seek algorithm; it SHALL be format-agnostic, storing whatever `SeekPoint`s a
decoder emits. The `Decoder` SHALL choose seek-point placement (member/stream start
vs. post-realignment) and MAY perform progressive index enrichment during `feed`
using the `inner` it retained from `recreate`, restoring `inner`'s position itself.
Forward-only codecs SHALL emit empty `points`, keep `pending_error` `None`, and
inherit the no-op `build_index`. Deferred truncation (e.g. unix-compress leftover
bits) SHALL surface through `pending_error`, raised on the next empty `read` after
delivering bytes; the stream SHALL clear it via `clear_pending_error` after raising
(and on seek reset). Adding a codec SHALL add a `Decoder` and MUST NOT require a new
stream subclass or a `SegmentedDecompressorStream` layer.

#### Scenario: wrapper surface matrix

| Case | Expected |
| --- | --- |
| Any read-only stream wrapper is used | Shared base supplies read-only surface and `readinto` / `readall` |
| Public codec stream is opened | Returned object is an `ArchiveStream` with stream presentation metadata |

#### Scenario: decoder composition matrix

| Case | Expected |
| --- | --- |
| Forward-only codec (zlib, brotli, ppmd, bcj, deflate64) | Implements `recreate`/`feed`/`flush`/`finished`; emits empty `points`; `pending_error` `None`; inherits no-op `build_index` |
| Segmented boundary codec (lzip, xz stream start) | `feed` emits a `SeekPoint` at the boundary with the codec's own before/after placement; stream stores it |
| Progressive enrichment (xz block index) | `feed` scans the completed stream's footer via retained `inner` and emits block `SeekPoint`s (carrying resume `state`); restores `inner` position |
| One-shot / forward walk (xz, lzip backward scan; future BGZF forward walk) | `build_index` returns points + size; stream drives it demand-driven per `seekable-decompressor-streams` |
| Deferred truncation (unix-compress leftover bits) | `pending_error` set after `flush`; base raises it on the next empty `read` |
| A new codec is added | One `Decoder` added; no new stream subclass; no `SegmentedDecompressorStream` layer |

### Requirement: Backend dispatch is separable from opening

The system SHALL allow callers to resolve a codec/configuration's open function
and matching exception translator independently of opening a stream, so detection,
TAR, and 7z folder pipelines reuse the same backend selection.

#### Scenario: backend dispatch matrix

| Case | Expected |
| --- | --- |
| Open function is requested for a codec/configuration | Function and matching exception translator are returned |

### Requirement: Decompression streams count compressed bytes consumed

The decompression layer SHALL expose a monotonically increasing count of
compressed bytes consumed from the underlying source, such as
`input_bytes_consumed`. The counter SHALL be cheap, available for non-seekable
pipes, and MUST NOT perturb bytes read or decompressed.

Archive readers SHALL surface the running total for a single outer compressed
source as `compressed_bytes_consumed`, returning `None` when no single compressed
source exists (uncompressed container, directory). When solid/streamed member
streams share that outer source, the count is cumulative across the archive.

#### Scenario: compressed-byte counter matrix

| Case | Expected |
| --- | --- |
| `.gz` read incrementally from non-seekable source | Count increases monotonically and is readable mid-stream |
| Uncompressed container or directory | `compressed_bytes_consumed is None` |
| Count is observed repeatedly during extraction | Decompressed output is byte-for-byte unchanged |
