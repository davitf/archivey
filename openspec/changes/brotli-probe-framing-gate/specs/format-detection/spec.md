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
| Brotli, first meta-block compressed, no corroborating extension | `PROBABLE` / `content_probe` |
| Brotli, first meta-block uncompressed/metadata, no corroborating extension | `GUESS` / `content_probe` |
| ZIP / TAR / ISO | Container backend `MAGIC`, merged into the same table |

### Requirement: Executable-looking prefixes must not silently become a wrong stream format

When a source's leading bytes look executable-shaped, detection SHALL NOT let a
content probe (notably Brotli) claim a stream codec and allow `open_archive` to
succeed with a fabricated single-file member (e.g. `*.uncompressed`). That is a
silent wrong answer.

This obligation is **outcome-shaped**, not "disable Brotli whenever the prefix is
`MZ`". A genuine Brotli (or other probe-matched) stream whose first bytes happen
to look executable MUST remain detectable.

The rule, settled by measurement in the archived `sfx-format-detection` design, grades the
evidence:

- A **weak** cue — a bare `MZ` or `\x7fELF` prefix — SHALL trigger the SFX scan and
  nothing else. When the scan finds no archive magic, content probes run unchanged. Two
  or four bytes are not proof, and refusing a probe on them would reject real streams.
- A **strong** cue — a DOS header whose `e_lfanew` points at a `PE\0\0` signature, or an
  ELF identification block with valid `EI_CLASS` / `EI_DATA` / `EI_VERSION` — with no
  archive magic in the window SHALL suppress content probes entirely; detection falls
  through to the extension guess or `FormatDetectionError`. A structurally confirmed
  executable is not a compressed stream.

The system SHALL NOT tighten the Brotli probe with a **threshold** to satisfy this
requirement: measured, a larger probe prefix does not reduce false positives (8.27% →
8.13% of random data at 16x the prefix) and requiring decoded output loses real streams
roughly one-for-one. That prohibition is about knobs traded against a false-positive rate,
and it does **not** reach a check derived from the format's own invariant, which costs no
real streams — see *A content probe SHALL NOT accept framing the source cannot hold*.

The residual — arbitrary non-archive data that the Brotli probe claims, which is a far
wider problem than executable prefixes — remains out of scope *here* and stays tracked
separately (`dev-docs/open-issues.md` P12, `dev-docs/threat-model.md` O10). The
**first-block** framing check narrows it from 3.5% of a real `/usr` tree to ~0.15%
(61/39 859 measured); the deferred chain walk would cut further to ~0.035%. It does not
close the residual, and the registered wording needs three clauses, not one: the listing
is wrong, a full read raises, and a prefix of fabricated bytes may already have been
produced.

#### Scenario: no silent wrong answer on executable-shaped prefix

| Case | Expected |
| --- | --- |
| Low-entropy `MZ` stub + RAR/7z/ZIP payload in window | Detected as that archive — **not** `BROTLI` / fabricated member |
| Real Brotli stream with non-executable prefix, `.br` extension | Unchanged — `BROTLI` / `PROBABLE` via content probe |
| Real Brotli stream with non-executable prefix, compressed-first, no corroborating extension | Still `BROTLI` via content probe, at `PROBABLE` |
| Real Brotli stream with non-executable prefix, uncompressed/metadata-first, no corroborating extension | Still `BROTLI` via content probe, at `GUESS` |
| Real Brotli (or other probe format) whose prefix coincides with a **weak** executable cue | Still detected as that stream — **not** forced to `FormatDetectionError` solely because two bytes were `MZ` |
| **Strong** executable cue (validated PE / ELF), no archive needle in the window | No content probe runs; extension guess or `FormatDetectionError` — never a fabricated member |
| Executable-shaped prefix, no archive needle, probe correctly rejects | Extension guess or `FormatDetectionError` — not a fabricated member |

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
confidence SHALL be `PROBABLE` when the file extension corroborates the format **or**
when the first meta-block is compressed, and `GUESS` when the only evidence is a
probe hit whose first meta-block is uncompressed or metadata. Brotli's probe is the
only one measured to accept ordinary files (3.5% of a real `/usr` tree before the
framing gate), and that false-positive mass concentrates in the uncompressed/metadata
first-block class — so those claims are weaker evidence than a compressed-first hit or
an extension-backed one. This does not change *what* is detected — only what the system
claims to know about it.

The zlib and LZMA Alone probes keep `PROBABLE` unconditionally. Both measured **0 false
positives in 20 000 random blobs**, so the confidence downgrade would cost honesty rather
than buy it. (Alone was additionally re-measured at 0 over 4 000 blobs of 64 KiB; its
real-world residual is a framing problem, not a confidence one — see the framing
requirement.)

Within Brotli, a probe-only hit whose **first meta-block is compressed** SHALL keep
`PROBABLE`: measured on random data, that class is accepted 0.014% of the time against
~100% for an uncompressed first block, and 25 of 25 real streams found in the wild are
compressed-first. An uncompressed or metadata first block is the class every false
positive comes from, and takes `GUESS`. This split is load-bearing for error provenance:
`format_unconfirmed` / `PROBE_FORMAT_UNCONFIRMED` apply only to `GUESS` failures, so a
compressed-first probe-only hit takes the corroborated failure path. Uncompressed-first
remains a valid stream class (incompressible payloads); the framing gate keeps those
streams — they are not rejected for being uncompressed-first.

The LZMA Alone probe SHALL attempt a bounded `FORMAT_ALONE` decode and MUST NOT
claim streams that already matched exact magic (notably lzip `LZIP` and xz
`FD 37 7A…`).

#### Scenario: content-probe matrix

| Case | Expected |
| --- | --- |
| No magic; bounded prefix decompresses as Brotli, name is `x.br` | `BROTLI`, `PROBABLE`, `content_probe` |
| No magic; bounded prefix decompresses as Brotli, first meta-block compressed, no corroborating extension | `BROTLI`, `PROBABLE`, `content_probe` |
| No magic; bounded prefix decompresses as Brotli, first meta-block uncompressed/metadata, no corroborating extension | `BROTLI`, `GUESS`, `content_probe` |
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
length is known, the Brotli probe SHALL reject a prefix whose **first** declared
meta-block violates that invariant. Detection supplies that length via the existing
cheap size probe (`source_byte_size`); when the length is unknown the check is skipped,
not guessed, and detection behaves as before.

A stronger **chain walk** — following byte-aligned self-describing meta-blocks and
rejecting a later link that overruns, or a declared end with trailing bytes — is
measured and sound (results doc; design Decision 2026-08-22) but is **not required by
this change**. It needs reads past the peeked prefix and is deferred to a follow-up
(`tasks.md` 5.7). This requirement is satisfied by the first-block check alone.

The same first-block principle SHALL apply to the **LZMA Alone** probe, whose only
measured real-world false positives are files that are *exactly* its 13-byte header: a
source no longer than the header carries no range-coder payload and cannot be an Alone
stream. Rejecting those removed 4 of 4 measured hits across 40 000 real files. This is
the same invariant, not a second heuristic — the framing a source declares must fit what
it holds.

No decompression beyond today's bounded prefix is required for the first-block check.

This requirement is about *soundness*, not tuning: it MUST NOT reject any complete valid
stream, so the real-stream corpus in `testing-contract` is the binding constraint.
Probe-parameter tuning (larger prefix, minimum decoded output, WBITS whitelists) SHALL
NOT be used in its place — each was measured to trade false positives for false negatives
roughly one-for-one, or to reject real `.br` files.

That distinction — a threshold traded against a false-positive rate, versus a check the
format's own framing already implies — is what *Executable-looking prefixes must not
silently become a wrong stream format* is stating when it forbids "tightening the Brotli
probe". The two requirements stand together; this one is the invariant, that one is the
prohibition on knobs.

#### Scenario: framing gate matrix

| Case | Expected |
| --- | --- |
| Real `.br` file whose first meta-block is compressed | Accepted (no declared length to check) |
| Real `.br` file whose first meta-block is uncompressed (incompressible payload) | Accepted — declared length fits by construction |
| `MZ` + `\x90`×4094 (declares 2 171 061 bytes, file is 4096) | Rejected — declared framing overruns the source |
| A `/**\n…` C header (declares an uncompressed block past EOF) | Rejected |
| Arbitrary data whose first declared block happens to fit | Probe may still accept; residual is accepted and documented (first-block-only floor; chain walk is follow-up 5.7) |
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
| Probe-only `GUESS` result, read fails | Same exception type; `format_unconfirmed=True`; message names unconfirmed identification; `PROBE_FORMAT_UNCONFIRMED` diagnostic |
| Probe + `.br` extension (`PROBABLE`), read fails | Ordinary truncation/corruption error — the format is corroborated; `format_unconfirmed=False` |
| Probe-only `GUESS` result, read succeeds | Success; no error, no downgrade |
