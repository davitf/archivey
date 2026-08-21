## MODIFIED Requirements

### Requirement: Magic/extension/probe tables are aggregated from backends and codec descriptors

Detector tables SHALL come from container backends (`ReadBackend.MAGIC` /
`EXTENSIONS` / `CONTENT_PROBES`) and stream-codec descriptors — no per-format
`detect()` logic. Stream-codec rows come from descriptors (not hand-listed on
`SingleFileBackend`). A content probe is the codec's `content_probe` function.
Detected formats and `detected_by` MUST match prior behavior. Confidence MUST also
match prior behavior **except** for an uncorroborated Brotli content-probe match,
which reports `GUESS` (see the magic-less-formats requirement); that is the only
confidence value this change moves.

#### Scenario: table sources matrix

| Case | Expected |
| --- | --- |
| `.gz` / `.zst` | Same result as before; magic from codec descriptors |
| zlib / LZMA Alone | `PROBABLE` / `content_probe` from descriptor functions — unchanged |
| Brotli, extension corroborates | `PROBABLE` / `content_probe` |
| Brotli, no corroborating extension | `GUESS` / `content_probe` |
| ZIP / TAR / ISO | Container backend `MAGIC`, merged into the same table |

### Requirement: Magic-less formats are detected by a content probe

When the magic-byte table yields no match, the system SHALL run each registered
content probe on the peeked prefix (consumes nothing). This covers Brotli (no
signature), zlib (too-unspecific CMF/FLG), and LZMA Alone (13-byte header whose
properties byte is too weak for exact magic). Probes typically decode a bounded
prefix; MAY gate on cheap structural bytes first; and MAY consult the source length
when detection knows it (see the framing requirement below). Skip when the
decompressor backend is missing (fall through to extension). Extension MAY override
a disagreeing probe (false-positive risk on short/adversarial input).

A probe match SHALL report `detected_by="content_probe"`. For **Brotli specifically**,
confidence SHALL be `PROBABLE` when the file extension corroborates the format and
`GUESS` otherwise: Brotli's probe is the only one measured to accept ordinary files
(3.5% of a real `/usr` tree), so a Brotli claim with nothing else agreeing is weaker
evidence than the other probes' claims. This does not change *what* is detected — only
what the system claims to know about it.

The zlib and LZMA Alone probes keep `PROBABLE` unconditionally. Both measured **0 false
positives in 24 000 random blobs**, so the confidence downgrade would cost honesty rather
than buy it.

Within Brotli, a probe-only hit whose **first meta-block is compressed** MAY keep
`PROBABLE`: measured on random data, that class is accepted 0.014% of the time against
~100% for an uncompressed first block, and 25 of 25 real streams found in the wild are
compressed-first. An uncompressed or metadata first block is the class every false
positive comes from, and takes `GUESS`.

The LZMA Alone probe SHALL attempt a bounded `FORMAT_ALONE` decode and MUST NOT
claim streams that already matched exact magic (notably lzip `LZIP` and xz
`FD 37 7A…`).

#### Scenario: content-probe matrix

| Case | Expected |
| --- | --- |
| No magic; bounded prefix decompresses as Brotli, name is `x.br` | `BROTLI`, `PROBABLE`, `content_probe` |
| No magic; bounded prefix decompresses as Brotli, no corroborating extension | `BROTLI`, `GUESS`, `content_probe` |
| zlib CMF/FLG + clean zlib decode | `ZLIB`, `PROBABLE`, `content_probe` — unchanged |
| zlib-looking header, decode fails | No zlib claim; fall through to extension / fail |
| `.br`, Brotli extra missing | Probe skipped; extension guess `BROTLI`/`GUESS` |
| No magic; bounded prefix decompresses as LZMA Alone | `LZMA_ALONE`, `PROBABLE`, `content_probe` — unchanged |
| Stream starts with `LZIP` | lzip magic wins; Alone probe not claimed |
| Alone-looking bytes that fail `FORMAT_ALONE` decode | No Alone claim; fall through |

## ADDED Requirements

### Requirement: A content probe SHALL NOT accept framing the source cannot hold

RFC 7932 lets a Brotli meta-block *declare* a length and then emit literal bytes: a
non-last uncompressed meta-block is a four-byte header after which the decoder copies.
A bounded-prefix decode therefore cannot distinguish a real stream from any data whose
first bytes happen to parse as such a header — measured at **8.2% of arbitrary binary
data** and **3.5% of a real `/usr` tree**, the latter dominated by files opening `/**\n`.

A **complete, valid** stream always satisfies
`header_bytes + declared_length <= source_length` for a declared (uncompressed or
metadata) meta-block, because those bytes must physically be present. When the source
length is known, the Brotli probe SHALL reject a prefix whose first declared meta-block
violates that invariant, and MAY follow the chain of byte-aligned self-describing
meta-blocks — whose successors' offsets are known without decompressing — rejecting a
link that overruns the source or that reaches the declared end with bytes left over.

The same principle SHALL apply to the **LZMA Alone** probe, whose only measured
real-world false positives are files that are *exactly* its 13-byte header: a source no
longer than the header carries no range-coder payload and cannot be an Alone stream.
Rejecting those removed 4 of 4 measured hits across 40 000 real files. This is the same
invariant, not a second heuristic — the framing a source declares must fit what it holds.

The check SHALL be skipped, not guessed at, when the source length is unknown (a
non-seekable stream longer than the peeked prefix); detection then behaves as before.
No decompression beyond today's bounded prefix is required, and the chain walk SHALL be
bounded in the number of links it follows.

This requirement is about *soundness*, not tuning: it MUST NOT reject any complete valid
stream, so the real-stream corpus in `testing-contract` is the binding constraint.
Probe-parameter tuning (larger prefix, minimum decoded output, WBITS whitelists) SHALL
NOT be used in its place — each was measured to trade false positives for false negatives
roughly one-for-one, or to reject real `.br` files.

#### Scenario: framing gate matrix

| Case | Expected |
| --- | --- |
| Real `.br` file whose first meta-block is compressed | Accepted (chain stops immediately) |
| Real `.br` file whose first meta-block is uncompressed (incompressible payload) | Accepted — declared length fits by construction |
| `MZ` + `\x90`×4094 (declares 2 171 061 bytes, file is 4096) | Rejected — declared framing overruns the source |
| A `/**\n…` C header (declares an uncompressed block past EOF) | Rejected |
| Arbitrary data whose declared block happens to fit | Probe may still accept; residual is accepted and documented |
| OLE/CFB file (`D0 CF 11 E0 A1 B1 1A E1`, ≥ 7425 bytes) | **Still accepted** — its constant magic declares MLEN 7422, which always fits. Known systematic residual, not a gate failure |
| COFF object (`64 86 …`, e.g. a Go `.syso`) | Still accepted — same shape |
| A 13-byte text file, LZMA Alone probe | **Rejected** — a source that is only the 13-byte header cannot be an Alone stream (removes the entire measured real-world Alone residual, 4 of 4) |
| Non-seekable source of unknown length | Gate skipped; today's behaviour |
| Source length known to be shorter than the declared metadata skip | Rejected |

### Requirement: A read failure on probe-only evidence names its provenance

When a single-file result's format came from a content probe with no corroborating
extension (`detected_by="content_probe"`, confidence `GUESS`), a decode failure while
reading the fabricated single member SHALL report that the format identification was
unconfirmed, rather than presenting as a plain truncation of a file whose format is
settled. Today such a file raises `TruncatedError (format=BROTLI)`, which blames the
source for being truncated and names a format it never was.

The system SHALL NOT refuse the open on this basis — a genuine extensionless stream that
the probe correctly identified must still be readable, and a probe-only result that reads
cleanly is a success.

#### Scenario: unconfirmed-format read failure

| Case | Expected |
| --- | --- |
| Probe-only `GUESS` result, read fails | Error names the unconfirmed identification; not a bare truncation |
| Probe + `.br` extension (`PROBABLE`), read fails | Ordinary truncation/corruption error — the format is corroborated |
| Probe-only `GUESS` result, read succeeds | Success; no error, no downgrade |
