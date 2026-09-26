## MODIFIED Requirements

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
