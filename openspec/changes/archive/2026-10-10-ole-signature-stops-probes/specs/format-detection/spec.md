## ADDED Requirements

### Requirement: A known non-archive signature stops the content probes

When the source starts with a recognized **non-archive signature**, detection SHALL NOT run
the content probes or the SFX scan, whatever executable cue the bytes raise, and SHALL fall
through to the extension guess. The magic tiers run as before. With no extension, the
`FormatDetectionError` SHALL name the evidence that stopped the probes, as it SHALL after
a strong executable cue. Today the one signature is OLE / Compound File Binary,
`D0 CF 11 E0 A1 B1 1A E1` at offset 0.

#### Scenario: non-archive signature

Both the Brotli and the LZMA Alone probe accept an OLE header followed by zeros (`.doc`,
`.xls`, `.ppt`, `.msi`, `Thumbs.db`), and neither can reject it on its own framing: the
header is a fitting Brotli chain, and a range coder decodes zero bytes without error.
This is not the threshold that *Executable-looking prefixes must not silently become a
wrong stream format* forbids: it is positive evidence of another format, and an
eight-byte signature at offset 0 is as specific as an archive's exact magic. A two-byte
prefix such as `MZ` is not specific enough. The SFX scan is skipped because these files
are not stubs: an archive stored inside one is part of the document, not its payload.

The cases are pinned in `tests/test_audit_backup_scan.py`: an OLE header over zeros is
`FormatDetectionError` naming the OLE signature, not `BROTLI` or `LZMA_ALONE`; an OLE file
named `x.br` is `BROTLI` / `GUESS` / `extension`; a ZIP inside an OLE file is not
reported; and a signature whose bytes raise an executable cue does not start the SFX
scan. The measurement is in `dev-docs/investigations/2026-10-backup-scan.md` §3.2.

## MODIFIED Requirements

### Requirement: Magic-less formats are detected by a content probe

When the magic-byte table yields no match, the system SHALL run each registered
content probe on the peeked prefix (consumes nothing), except after a strong executable
cue or a known non-archive signature (see *A known non-archive signature stops the content
probes*). This covers Brotli (no
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
positive comes from, and takes `GUESS`. This split grades evidence strength only —
`format_unconfirmed` / `PROBE_FORMAT_UNCONFIRMED` key on probe-only provenance (and
`EXTENSION_FORMAT_UNCONFIRMED` on an extension-only guess), not on confidence (see
*Detection confidence SHALL NOT be the trigger for error provenance*).
Uncompressed-first remains a valid stream class (incompressible payloads); the framing
gate keeps those streams — they are not rejected for being uncompressed-first.

The **zlib** probe SHALL gate on the RFC 1950 header grammar — `CM == 8`, `CINFO <= 7`,
and `(CMF * 256 + FLG) % 31 == 0` — rather than an allow-list of common headers, and SHALL
accept a header with `FDICT` set. All seven legal window sizes are therefore recognised.
`FDICT` is accepted rather than rejected because a preset dictionary is valid zlib and the
decode, not the header, decides what archivey can read: today no dictionary is ever
supplied to the codec layer, so every `FDICT` stream fails that decode and falls through.
The gate does not encode that as a rejection — the day a dictionary can be supplied, the
grammar already admits the header.

The LZMA Alone probe SHALL attempt a bounded `FORMAT_ALONE` decode and MUST NOT
claim streams that already matched exact magic (notably lzip `LZIP` and xz
`FD 37 7A…`). It SHALL NOT reject a **dictionary size** of zero: every 32-bit value is
legal and decoders round values below 4 KiB up to 4 KiB. Rejecting zero was an ordering
workaround for a zero-filled ISO system area, which the far-magic step of the detection
algorithm now answers before any probe runs.

It SHALL, separately, refuse a header declaring an **uncompressed size** of exactly zero.
Any value but the all-ones "unknown" sentinel is the stream's exact output length, so zero
declares a stream carrying no payload — nothing that can be opened. This is the same rule
as the probe's existing refusal to claim an empty successful decode, stated at the header
because a bounded probe cannot reach it: 18 zero bytes are a *valid, complete, empty*
Alone stream (a legal 13-byte header plus a five-byte range-coder init), so zero-filled
padding decodes cleanly to nothing and the bounded read then reports truncation, which is
otherwise a match. This refuses nothing that was ever detected: an empty stream is not
claimed with or without the rule, because the probe already declines an empty decode, and
a `.lzma` name still opens one through the extension. It is a rule about *zero output*,
not about the sentinel — a header carrying a real uncompressed size, as the LZMA SDK's
encoder writes, is as welcome as one carrying the sentinel. The two header fields are
independent: a stream with a zero dictionary size and a real payload is still detected.

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

#### Scenario: header grammars accept the full legal range

| Case | Expected |
| --- | --- |
| zlib stream at any window size 512 B – 32 KiB (`18 95`, `28 91`, `38 8d`, `48 89`, `58 85`, `68 81`, `78 9c`) | `ZLIB`, `content_probe` — all seven |
| zlib header with `FDICT` set | Header passes the grammar; the decode decides. Archivey supplies no preset dictionary today, so the decode fails and no zlib claim is made |
| Header failing `CM == 8`, `CINFO <= 7` or the mod-31 check (e.g. all-zero bytes) | No zlib claim; no decode attempted |
| LZMA Alone stream whose dictionary-size field is zero | `LZMA_ALONE`, `content_probe` |
| Alone header declaring an uncompressed size of exactly zero | No Alone claim; no payload to open |
| Alone header carrying a real uncompressed size rather than the sentinel | `LZMA_ALONE`, `content_probe` — unaffected |
| Zero-filled source of any length (padding, a sparse or zero-truncated file) | No Alone claim — the header declares zero output |
| Zero-filled source with `CD001` at 32 769 | `ISO` at the far-magic step; no Alone claim |

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
required by its own requirement, *A content probe SHALL follow a format's self-describing
block chain*, which supersedes the deferral this paragraph used to record. This
requirement remains satisfied by the first-block check alone; the walk is what covers
sources large enough for the first-block check to go vacuous.

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
| Arbitrary data whose first declared block happens to fit | Probe may still accept at *this* requirement's floor; the residual is then narrowed by *A content probe SHALL follow a format's self-describing block chain* below |
| OLE/CFB file (`D0 CF 11 E0 A1 B1 1A E1`, ≥ 7425 bytes) | Brotli first-block gate / `BrotliCodec.content_probe` still accept (MLEN 7422 always fits). End-to-end `detect_format` does not run the probes: the OLE signature stops them (*A known non-archive signature stops the content probes*) |
| COFF-shaped prefix (`64 86 …` with a fitting uncompressed trailer) | Brotli gate accepts. End-to-end `BROTLI` at `GUESS`: the Alone probe declines the header's zero uncompressed size |
| A 13-byte text file, LZMA Alone probe | **Rejected** — a source that is only the 13-byte header cannot be an Alone stream (removes the entire measured real-world Alone residual, 4 of 4) |
| Non-seekable source of unknown length (≥ `DETECTION_LIMIT` peek) | Gate skipped; today's behaviour |
| Non-seekable source shorter than the detection peek | Length inferred from the short peek; gate applies |
| Source length known to be shorter than the declared metadata skip | Rejected |

### Requirement: A content probe SHALL follow a format's self-describing block chain

**Scope: formats whose blocks are byte-aligned and self-describing, so a successor's offset
is known without decompressing. Brotli is the only such format today.** For those, a probe
SHALL follow the chain to test the same framing invariant beyond the first block, and SHALL
reject a link that overruns the source or a declared end that leaves trailing bytes. A
format outside that scope is unaffected — this is not an obligation on every probe.

The walk is mandatory rather than optional because the alternative is a probe whose
false-positive rate silently depends on whether an implementer felt like walking. What is
*bounded* is the work, not the obligation: the budgets below are the escape hatch, and
exhausting either is a defined outcome rather than a licence to skip the walk.

The walk exists because the first-block check goes **vacuous on large sources**: Brotli's
MLEN field tops out at 2²⁴, so past ~16 MiB every declared length fits trivially. Measured
on random blobs, the walk takes 16 MiB acceptance from 8.33% (where the first-block check
buys nothing) to 2.00%, and a `/usr` tree from 61 survivors to 14.

The walk SHALL be **bounded by a declared link count** (today: 8) and, on forward-only
sources, by a **declared maximum absolute offset** for probe reads (today: 1 MiB). Reaching
either means *cannot disprove*: the probe SHALL keep the verdict the earlier rules reached
and MUST NOT reject on that basis. This is the same discipline as an unknown source length
— absence of evidence is not evidence against, so budget exhaustion can never manufacture a
false negative. The 1 MiB figure is the memory-governing ceiling for a non-seekable
`read_at` (buffering `[0, offset)`); seekable sources and paths may seek past it.

The walk stops at the first compressed block, which carries no declared length to check.
On a real Brotli file whose first meta-block is compressed — 79 of 150 in the corpus — it
therefore terminates immediately, having read four bytes.

Following the chain requires bytes at offsets that may lie past the peeked prefix.
However a probe reaches them, the reads SHALL stay within the declared bounds and
SHALL NOT decompress.

#### Scenario: chain walk matrix

| Case | Expected |
| --- | --- |
| Real `.br` file, first meta-block compressed | Walk stops at once; accepted |
| Real `.br` file, uncompressed first block, all links fit | Accepted — every declared length is honoured |
| Fabrication whose first block fits but whose second link overruns the source | **Rejected** |
| Fabrication whose chain reaches a declared end with bytes left over | **Rejected** |
| 16 MiB source whose first declared block fits trivially (MLEN ceiling) | Walk decides; first-block check alone would have accepted |
| Chain longer than the link bound | Verdict unchanged from the earlier rules; **not** a rejection |
| Non-seekable `read_at` past the 1 MiB offset ceiling | Declined → cannot disprove; earlier verdict stands |
| OLE/CFB file ≥ 7425 bytes | The probe still accepts it — its constant magic yields a fitting chain. Detection does not run the probe on it (*A known non-archive signature stops the content probes*) |
