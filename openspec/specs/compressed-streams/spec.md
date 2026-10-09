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
`EOFError` SHALL map to `TruncatedError`, except for a declared window the decoder
refuses: a window over `DecoderLimits.max_decoder_memory` SHALL raise
`ResourceLimitError`, and a window over libzstd's own ceiling, which no cap lifts, SHALL
raise `UnsupportedFeatureError`.

A liblzma failure that is not damage SHALL NOT surface as `CorruptionError`: a filter,
filter option or integrity check liblzma cannot decode SHALL raise
`UnsupportedFeatureError`, and a decoder memory cap refusing a declared dictionary
SHALL raise `ResourceLimitError`. These are not `ReadError`s, so a listing that meets
one fails rather than publishing the members before it as an incomplete report.

A source that ends before its first complete header is end-of-input too. For the
native xz, lzip and unix-compress decoders, a source that is empty, or holds only a
prefix of the format's magic bytes, SHALL raise `TruncatedError`; a short source whose
bytes cannot begin that format SHALL raise `CorruptionError`. Neither SHALL decode as a
valid empty stream.

#### Scenario: decompression error matrix

| Case | Expected |
| --- | --- |
| Corrupt compressed stream is read | `CorruptionError` with backend exception as `__cause__` |
| Compressed stream ends mid-data | `TruncatedError` |
| Zstd stream ends before end-of-frame marker | `TruncatedError`, not a silent short read |
| Zstd checksum frame is corrupted | `CorruptionError` with backend `ZstdError` as `__cause__` |
| Empty source, or only a prefix of the magic, to xz / lzip / unix-compress | `TruncatedError` |
| Source shorter than a header whose bytes are not the format's magic (xz / lzip / unix-compress) | `CorruptionError`, never `b""` |
| xz block header (valid CRC) names a filter liblzma does not know | `UnsupportedFeatureError`, not `CorruptionError` |
| xz / LZMA stream declares a dictionary above `DecoderLimits.max_decoder_memory` | `ResourceLimitError` |
| zstd frame declares a window above `DecoderLimits.max_decoder_memory` | `ResourceLimitError` |
| zstd frame declares a window above libzstd's ceiling (2^31 on a 64-bit build) | `UnsupportedFeatureError`, naming no cap to raise |

### Requirement: Content faults raise from read, never from close

This requirement scopes to the streams this layer owns: `DecompressorStream`
(and every codec `Decoder` behind it) and `VerifyingStream` / the fused
`MemberVerifier`. Other backends (the rapidgzip accelerator and its
`_GzipTruncationCheckStream`, and any third-party wrapper) are **out of scope**
here; they already surface content faults from `read` rather than `close`, and
retargeting them is deferred (see the rapidgzip follow-up). The wording below is
a standing rule for the in-scope streams, not a claim that every stream type in
the library has been audited to it. The one exception is the paragraph on accelerated
decoders and its three `Accelerated decoder` matrix rows, which are deliberately
normative for the in-process bzip2 accelerator and the rapidgzip decoder process.

Decode and verify streams SHALL raise content `TruncatedError` and
`CorruptionError` from `read` / `readall` (and from size/seek paths that would
otherwise report a false clean completion). `close()` MUST NOT raise those
content faults. `close()` MAY still propagate teardown failures (`OSError`,
translated inner-close errors).

Public bounded `read(n)` for `n ≥ 1` on `ArchiveStream` and
`VerifyingStream` / `MemberVerifier` SHALL be **full-count**: return exactly `n`
bytes unless a terminal boundary is reached (clean EOF, truncation-shaped short,
or a raised content error). Implementations SHALL issue one `inner.read(n)` and
forward a short non-empty return as terminal — they SHALL NOT retry it, which would
pull a decoder's deferred truncation into the call. The `n`-or-terminal guarantee
therefore rests on inners being fill-or-EOF (`DecompressorStream`, `ZipExtFile`,
`BytesIO`); an inner that may short mid-stream SHALL be read through a full-count
reader in front (at the source boundary, the `ArchiveSource`) rather than a gather loop
at the public surface. `read(0)` is a no-op, never EOF.

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

A seek off the sequential frontier SHALL forfeit digest verification until a seek to
position 0, which SHALL re-arm every check: the digests start again and the read
frontier is cleared, so a read from position 0 to the end after any seeks is
verified as a first read is, for every format. Length / truncation / over-run checks
SHALL remain active and SHALL key off bytes actually read (not a seek-updated
logical position alone). When a seek jumps the logical position to/past the declared
size without reading the intervening bytes, concluding SHALL read that skipped gap
(bounded by the declared size) **and probe one byte past the declared size**,
reproducing the same length + over-run verdict a sequential reaching read runs,
rather than returning `b""` blind. So a past-EOF `seek(declared_size)` on a
**truncated** member MUST NOT silence `TruncatedError`, and on an **over-long**
member (one that decodes past its declared size) MUST NOT silence `CorruptionError`.
Symmetrically, the same jump on a **complete** member MUST NOT fabricate either
fault: a seek to/past the declared size followed by `read` returns `b""` (standard
`BinaryIO` past-EOF semantics), and the `seek(member.size); read(1)` completeness
idiom works. A member already read to its declared size is length-verified, so a
later seek past the end concludes with no extra reads, unless a seek to 0 has
re-armed the checks since.

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

Once the public `ArchiveStream` has raised a content verdict (`CorruptionError` or
`TruncatedError`, or an error raised from one, such as ZipCrypto's password-or-damage
`EncryptionError`), every later `read` / `readinto` SHALL raise the same error object
again until the caller seeks, with the traceback it was first raised with rather than
one that grows per call. A seek SHALL succeed and restart the decode, so the prefix
reads again, as a truncated `DecompressorStream` does; the read that then reaches the
end SHALL raise the verdict again and return no bytes, whether or not the seek forfeited
the digest check (a seek to 0 re-arms it, and the damage found again raises the same
error object). A read reaches the end when it returns short or empty, is `read(-1)`, or
leaves the stream at or past the member's declared size; a full-length
`read(member.size)` after `seek(0)` is one. A caller who catches the verdict and seeks
back SHALL NOT read the damaged member as complete, clean data. `tell()`, `seekable()`
and `close()` are not gated by the verdict, and `close()` still does not raise it.
Opening the member again gives a fresh stream.

An accelerated decoder (the in-process bzip2 accelerator, or the rapidgzip decoder
process) SHALL leave `tell()` at the bytes the caller received after a read that raised,
by moving the decoder back to where that read started. When it cannot, its position
matches no byte the caller received, and the stream SHALL be given up rather than read
on from past a gap. That is the case when the caller's own source raised during a read or
a seek (the decoder took the fault for the end of its input), or when a read raised and
the decoder could not be moved back. The call that met the fault SHALL raise it: the
source's error, or the read's own. From then on, for the life of the stream, every
`read`, `readinto`, `seek` and `tell()` that reaches the decoder SHALL raise `ReadError`
with a message naming the cause. This narrows the paragraph above: after a verdict on a
given-up stream, a seek raises `ReadError` instead of restarting the decode, and
`tell()` raises too. `close()` SHALL still succeed. Opening the member again gives a
fresh stream.

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
| Any seeks, then `seek(0)` and a read to the end over a digest mismatch | Checksum re-armed by the seek to 0; `CorruptionError` |
| Seek to/past declared size on a **complete** member, then `read` (incl. `seek(size); read(1)`) | Returns `b""`; no fabricated `TruncatedError` (checksum forfeited by the seek) |
| Seek to/past declared size on a **truncated** member, then `read` | Concluding reads the skipped gap; `TruncatedError` with the true recoverable length |
| Seek to/past declared size on an **over-long** member, then `read` | Concluding reads the gap and probes past the declared size; `CorruptionError` (over-run), not a silent `b""` |
| Partial read then `close` before clean EOF (verify) | No digest/length verdict |
| Inner teardown fails on `close` | Teardown error may propagate |
| `ArchiveStream` raised a content verdict; caller catches it, then `read()` | Raises the same error object again; no bytes returned |
| Same, then `seek(0)` and a bounded `read(n)` inside the member | Seek succeeds; the prefix bytes are returned |
| Same, then `seek(0)` and a read that reaches the end | Raises the same error object again; that call returns no bytes |
| Same, retried many times | Traceback stays the first one; it does not grow per call |
| Same, `tell()` or `close()` | Not gated; `close()` does not raise the verdict |
| Accelerated decoder; a read raises and the decoder moves back | `tell()` equals the bytes delivered; the stream stays usable |
| Accelerated decoder; a read raises and the decoder cannot be moved back | That read raises its own error; every later `read` / `readinto` / `seek` / `tell()` raises `ReadError` naming the cause; `close()` succeeds |
| Accelerated decoder; the caller's source raises during a read or a seek | That call raises the source's error; then as the row above, even if the source recovers |

### Requirement: Decompressed output digests are verified at clean EOF

The verification stage SHALL compute available expected digest algorithms
incrementally over decompressed bytes and raise `CorruptionError` for a
computable mismatch at clean EOF. A mismatch SHALL surface from the terminal read
after all data chunks have been delivered; a bytes-returning full read raises and
returns no bytes. Partial/random-access reads SHALL NOT produce a digest verdict.

Supported computable algorithms SHALL include `crc32` (via `zlib.crc32`), the
`hashlib.algorithms_available` set, and `blake2sp` (the 8-way parallel BLAKE2s tree
hash used by RAR5), computed via an internal zero-dependency hasher. A well-formed
member carrying only a `blake2sp` digest SHALL therefore be verified, not skipped. A
zlib stream's Adler-32 trailer is checked by the decompressor, not by a verifying
stream.

When an expected digest cannot be computed because the algorithm is genuinely unknown
or a backend is missing, the system SHALL emit `DIGEST_UNVERIFIABLE` with algorithm,
non-secret reason, and member identity when available. Diagnostic policy controls
collection, logging/callback delivery, member attachment, and escalation. An xz stream
whose header names a check liblzma cannot compute SHALL likewise emit
`DIGEST_UNVERIFIABLE` and keep decoding; a stream declaring no check (ID 0) SHALL NOT.

#### Scenario: digest matrix

| Case | Expected |
| --- | --- |
| Expected `blake2sp` on a well-formed RAR5 member | Computed and verified; mismatch raises `CorruptionError` |
| Expected digest under a genuinely-unknown algorithm name | `DIGEST_UNVERIFIABLE` counted/retained/logged; bytes still returned without that check |
| Full member read reaches EOF with computable digest mismatch | `CorruptionError` naming the algorithm |
| Chunked read reaches EOF with mismatch | All valid chunks delivered; following terminal read raises |
| Caller abandons stream before clean EOF | No digest verdict or mismatch exception |
| xz stream header names check ID 2 (liblzma cannot compute it) | `DIGEST_UNVERIFIABLE`; bytes still returned unverified |
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
Forward-only codecs SHALL emit empty `points` and inherit the no-op `build_index`.
Deferred truncation (e.g. unix-compress leftover bits, or a zlib, gzip, raw deflate or
xz stream cut short) SHALL surface through `pending_error`. A chunked `read(n)` SHALL
return the bytes decoded before it and raise it on the next empty read; a whole-stream
`read()` SHALL raise it without returning the prefix. The stream SHALL clear the
decoder's copy via `clear_pending_error` after raising (and on seek reset), and SHALL
record the error it raised. Until a seek, every later read with no buffered bytes
SHALL raise that same error, as SHALL a `seek(0, SEEK_END)` that has no size from an
index, and the decode SHALL publish no size. A seek SHALL restart the decoder from the
nearest seek point, which reaches the same error again at the same place. Adding a
codec SHALL add a `Decoder` and MUST NOT require a new stream subclass or a
`SegmentedDecompressorStream` layer.

#### Scenario: wrapper surface matrix

| Case | Expected |
| --- | --- |
| Any read-only stream wrapper is used | Shared base supplies read-only surface and `readinto` / `readall` |
| Public codec stream is opened | Returned object is an `ArchiveStream` with stream presentation metadata |

#### Scenario: decoder composition matrix

| Case | Expected |
| --- | --- |
| Forward-only codec (zlib, brotli, ppmd, bcj, deflate64) | Implements `recreate`/`feed`/`flush`/`finished`; emits empty `points`; inherits no-op `build_index`; `pending_error` set only when the input ends incompletely |
| Segmented boundary codec (lzip, xz stream start) | `feed` emits a `SeekPoint` at the boundary with the codec's own before/after placement; stream stores it |
| Progressive enrichment (xz block index) | `feed` scans the completed stream's footer via retained `inner` and emits block `SeekPoint`s (carrying resume `state`); restores `inner` position |
| One-shot / forward walk (xz, lzip backward scan; future BGZF forward walk) | `build_index` returns points + size; stream drives it demand-driven per `seekable-decompressor-streams` |
| Deferred truncation (unix-compress leftover bits; a DEFLATE-family or xz stream cut short) | `pending_error` set after `flush`; `read(n)` raises it on the next empty read, `read()` at once |
| Stream that already raised its deferred error | Later reads with no buffered bytes, and `seek(0, SEEK_END)` without an index size, raise the same error; the decode publishes no size; a seek re-decodes from the nearest seek point |
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

### Requirement: An accelerator preserves the error contract of the path it replaces

When an optional accelerator backend (today rapidgzip, including its bundled bzip2
decoder) replaces a codec's default decoder, the accelerated stream SHALL raise the same
class of translated error, on the same inputs, as the non-accelerated path. An accelerator
SHALL NOT convert a decode failure into a successful empty read.

Specifically, a decoder that ends a stream having produced no output, without consuming its
input and without reaching a valid end-of-stream marker, SHALL raise rather than report
end-of-file. Where the accelerator cannot report enough to tell that apart from a genuine
empty stream (rapidgzip's bundled bzip2 decoder cannot), the first empty read before any
output SHALL be re-decoded by the non-accelerated decoder over a fresh view of the
source, which raises or confirms the empty stream. A seek before that first read does not
bypass the check: on such a stream the accelerator clamps the seek to 0. Accelerator mode
is a performance choice and SHALL NOT be observable as a difference in whether a corrupt
source raises, with one exception. Where every byte of output is covered by checks the
data itself declares, those checks give the verdict, and an accelerator MAY differ from
the standard-library decoder on stream-boundary malformations they cannot see:

- for a container member that declares its size and CRC (a ZIP member, a 7z coder
  under a CRC-checked file), a second stream or
  trailing bytes inside the member's compressed data, which the accelerator MAY read as
  content where the standard-library decoder stops at the first stream's end; the
  declared size and CRC then decide, so output that matches both reads and output that
  breaks either raises;
- for a standalone multi-member gzip, a wrong ISIZE on a member other than the last,
  when every member's CRC-32 is still checked.

A wrong ISIZE on the last member of a one-member gzip is not among them, whatever follows
the member. The accelerator reads the output through a CRC-32 and finds the trailer as the
one place near the end of the file where that CRC-32 occurs: it looks from 72 bytes before
the end of the non-zero data to 12 bytes after it, and takes the file as ending with the
trailer only when the CRC-32 occurs there once, followed by the length and then by nothing
but zero padding. A forged copy of those eight bytes, whether appended after a wrong
trailer, overlapping it or reaching back into the compressed data, leaves the real CRC-32
as a second occurrence, so it does not stand in for the trailer. Not excluded: a copy more
than 56 bytes after the real trailer, which needs rapidgzip to read past that many bytes
that are not a further member without an error; rapidgzip 0.16 raises on ten or more. Two
limitations remain, both of the further-member scan that decides a multi-member file (the
CRC-32 of the whole output is not the last member's, so it finds no trailer there): the
scan stands down at the first further member it confirms, so with three or more members a
wrong ISIZE on the last one reads clean too; and a further member counts once zlib has
decoded 64 KiB of its input without an error, so a large last member's wrong ISIZE is not
seen either. These hold where the rapidgzip build does not compare the size itself; one
that does (its own chunk decoder, as in the macOS wheels) raises, and the accelerator
gives the standard library's error. When a seek has skipped output, there is no CRC-32 of
it, and the last four bytes of the file stand in as the ISIZE.

#### Scenario: accelerator error parity

| Source | Accelerator `OFF` | Accelerator `AUTO` |
| --- | --- | --- |
| Valid bzip2 stream | Content | Content |
| Valid **empty** bzip2 stream | `b""` | `b""` |
| bzip2 source of 40 000 zero bytes | `CorruptionError` | `CorruptionError` — not `b""` |
| Zero-byte bzip2 source | `TruncatedError` | `TruncatedError` — not `b""` |
| Corrupt gzip source | `CorruptionError` | `CorruptionError` |

#### Scenario: the parity holds through the public reader

| Case | Expected |
| --- | --- |
| `open_archive(corrupt.bz2, seekable_members=True).read(member)` | Raises, matching `seekable_members=False` |
| A capability flag (`seekable_members`) | Never changes whether a corrupt source raises, except through the accelerator on the stream-boundary malformations listed above |

#### Scenario: a ZIP member with a second stream inside its compressed data

| Member | Accelerator `OFF` | Accelerator `ON` |
| --- | --- | --- |
| Two DEFLATE or bzip2 streams; declared size and CRC cover both | `TruncatedError` (decoder stops after the first) | Both streams' content |
| Two streams; declared size and CRC cover both sizes but the CRC is the first stream's | `TruncatedError` | `CorruptionError` (CRC) |
| Two bzip2 streams; declared size and CRC cover the first | First stream's content | `CorruptionError` (output past the declared size) |
