## RENAMED Requirements

- FROM: `### Requirement: Short-returning source coverage for seekable sources`
- TO: `### Requirement: Short-returning source coverage`

## MODIFIED Requirements

### Requirement: Non-seekable stream coverage for streaming backends

The system SHALL test every backend that supports streaming with a **non-seekable double**
that raises `io.UnsupportedOperation` from `seek()` and reports `seekable()` as `False`. A
streaming-capable backend MUST read and iterate correctly without repositioning the source.

The double SHALL be named in `tests/`, not in this spec. Naming a concrete class here is
what let the spec drift: it named `FakeNonSeekable` — a class that has never existed in
`tests/` — while the double actually used is `NonSeekableBytesIO`. It also described the
double as raising on *every* `seek` **and `tell`** call, which `NonSeekableBytesIO` does not
do: it answers `tell()` from its inner `BytesIO`. Answering `tell()` is load-bearing rather
than an oversight — `ConcatenatedFile.__init__` probes `tell()` before `seek()`, and
`fix_stream_start_position` needs a position for the mid-stream origin contract — so the
requirement is stated on `seek()` and `seekable()`, which is what the doubles enforce and
what backends branch on.

A backend that requires a seekable source, including ZIP and ISO per
`access-mode-and-cost` and the format specs, SHALL fail fast at open time with
`StreamNotSeekableError`. It MUST never implicitly buffer the non-seekable source to
make it seekable. The recovery path is to provide a seekable source for
seek-required formats, or to use a streaming format/path where applicable.

#### Scenario: non-seekable-source matrix

| Case | Expected |
| --- | --- |
| ZIP opened through a non-seekable double | `open_archive` raises `StreamNotSeekableError` at open; no member data read; never implicitly buffers |
| ISO opened through a non-seekable double | `StreamNotSeekableError` at open under the same seek-required rule |
| `.tar.gz` opened through a non-seekable double | Members are iterable and data is readable without seek/tell |
| Caller needs ZIP from non-seekable input | Use a seekable source; do not rely on Archivey buffering |

### Requirement: Short-returning source coverage

The system SHALL test archive opening from a source whose `read(n)` returns
fewer than `n` bytes on healthy, non-terminal data — the `io.RawIOBase` *up-to-n*
contract, which sockets, FUSE-backed files, and caller wrappers all exercise —
across **both** the seekable and the non-seekable source kinds. A short-returning double of
each kind SHALL cap **both** `read` and `readinto` at one byte, the worst legal case; a
double that delegates to `BytesIO` does not exercise this, since `BytesIO` is always
full-count, and a double that is short-returning *or* non-seekable but not both leaves the
non-seekable boundary untested.

A healthy archive read from such a source MUST open, list, and read back **identically**
to the same bytes from a full-count source. It MUST NOT raise `CorruptionError` or
`TruncatedError`: neither is an honest answer for an intact archive. Coverage SHALL span
every format in the declarative corpus, the committed RAR / ZIP / 7z fixtures, and
`open_stream` in both `seekable` modes — accelerators read the source themselves, so the
seekable mode is a distinct path.

Non-seekable coverage SHALL exercise each streaming-capable format **both with and
without an explicit `format=`**, because the two entry points differ. In `open_archive`,
detection wraps a non-seekable source in `PeekableStream` — which is itself full-count —
inside the `format is None` branch only, so an explicit `format=` skips that wrap and the
boundary is the only thing left. In `open_stream` the wrap is unconditional, so
`PeekableStream` is present either way and is today the *only* coalescing layer on that
path. A suite that tests only the detected `open_archive` path therefore leaves the
boundary's own guarantee unverified. Coverage SHALL NOT be satisfied by a backend whose third-party reader happens to
coalesce internally (stdlib `tarfile._Stream` does): at least one case SHALL assert the
boundary directly, on the stream `ensure_full_count_reads` returns.

Assertions SHALL be **parity against a full-count open of the same bytes**, not
hardcoded expectations, so a format that cannot be built or read in a given environment
matches on both sides instead of needing a skip.

Backends SHALL NOT rely on the source boundary alone: archivey's own fixed-size
structure reads (RAR3/RAR5 block headers, RAR encrypted-header AES blocks, ZIP local
headers) SHALL gather with `read_exact`, and SHALL be covered by driving the parser
directly from a short-returning source, since parsers also read through decrypt wrappers
and views that no boundary buffer sits in front of.

#### Scenario: short-returning-source matrix

| Case | Expected |
| --- | --- |
| Each declarative-corpus format, `max_chunk=1` | Listing, member types/sizes/link targets, and member bytes match the full-count open |
| Each committed RAR / ZIP / 7z fixture that opens from a full-count source | Same parity; a fixture that does not open standalone (volume part, deliberately broken) skips |
| `open_stream`, each raw-stream format × `seekable=False` and `True` | Decoded bytes match the full-count open |
| `parse_rar_archive` driven directly from a short-returning source | `header_offset` / `header_size` / `data_offset` / `compress_size` identical — a coalescing layer must report the logical position, not a buffer position |
| Healthy archive, short-returning source | Never `CorruptionError` / `TruncatedError` |
| Each streaming-capable format, `ShortReadNonSeekable(max_chunk=1)`, detected and with explicit `format=` | Both match the full-count open; the explicit-`format=` case does not depend on `PeekableStream` being in the chain |
| `ensure_full_count_reads(non_seekable_short_source).read(n)` | Returns exactly `n` bytes short of EOF, and consumes exactly `n` bytes from the source |
| The returned boundary stream over a non-seekable source | `seekable()` is `False`; no read-ahead is buffered |
| `read(-1)` / `readall()` on the boundary stream over a non-seekable short-returning source | Returns every remaining byte, and keeps doing so when the inner also returns short on `read(-1)` — the drain must not depend on the inner's `readall()` |
