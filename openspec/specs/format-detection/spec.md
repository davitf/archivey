# Format Detection

## Purpose

Identify archive format of a path or binary stream without fully opening it.
Returns frozen `FormatInfo` (format, confidence, encoding hint, optional SFX
offset, detection diagnostics). Detection never discards bytes the opener still
needs.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Auto-detect inside `open_archive`; caller sees events on `reader.diagnostics` |
| `diagnostics` | Collector/budget/policy; this spec owns the open handoff |
| `backend-registry` | Container `MAGIC` / `EXTENSIONS` / `CONTENT_PROBES` |
| `compressed-streams` | Codec descriptors supply stream-codec magic/probes |
## Requirements

### Requirement: detect_format() returns a FormatInfo

The system SHALL expose:

```python
archivey.detect_format(
    source: str | Path | BinaryIO,
    *,
    config: ArchiveyConfig | None = None,
) -> FormatInfo
```

```python
class DetectionConfidence(Enum):
    CERTAIN = "certain"
    PROBABLE = "probable"
    GUESS = "guess"

@dataclass(frozen=True)
class FormatInfo:
    format: ArchiveFormat
    confidence: DetectionConfidence
    detected_by: str
    encoding_hint: str | None
    payload_offset: int = 0
    diagnostics: DiagnosticSummary = DiagnosticSummary.empty()
```

`config=None` → library default. `confidence` = magic / structural probe /
extension-guess. `encoding_hint` is format-signal only (never a member scan).
`payload_offset > 0` marks an SFX payload start.

**Collectors:**

| Path | Behavior |
| --- | --- |
| Standalone `detect_format` | One finite collector; policy/callback/logging/budget; final summary on `FormatInfo.diagnostics` |
| Inside `open_archive` | Open creates prospective-reader collector + detection watermark, passes that collector into detection. On success the reader owns it — no seed/merge/replay/copy; each retained occurrence charged once. Internal detection-range `FormatInfo.diagnostics` is not retained after handoff; same events remain on the reader's cumulative summary |

#### Scenario: detect / handoff matrix

| Case | Expected |
| --- | --- |
| Standalone detect with magic/extension conflict | `FormatInfo.diagnostics` has exact conflict count + retained detail under default budget |
| Auto-detect inside `open_archive` retains conflict, open succeeds | Reader continues same collector/order/budget; no copied aggregate |
| Magic match | `confidence=CERTAIN`, `detected_by="magic"` |
| Extension-only guess | `confidence=GUESS`, `detected_by="extension"` |
| Explicit `diagnostic_policy` on detect | IGNORE/COLLECT/RAISE applies to that finite detection |

### Requirement: Magic-first detection with extension fallback and confidence scoring

The system SHALL execute format detection with this algorithm:

1. Read up to `DETECTION_LIMIT` bytes (default 4096) from the source.
2. Match against the magic-byte table (exact offsets).
3. Match → `CERTAIN` / `detected_by="magic"`.
4. Else, if `Path` with known extension → `GUESS` / `detected_by="extension"`.
5. Else → content probes, then fail (`FormatDetectionError` when nothing matches).

#### Scenario: unrecognised bytes, no path

| Case | Expected |
| --- | --- |
| Non-seekable `BinaryIO`, no filename, no magic | `FormatDetectionError` |

### Requirement: Conflict resolution — magic wins and warning is emitted

Magic/content result wins per existing precedence. A genuine mismatch SHALL emit
`FORMAT_EXTENSION_CONFLICT` with typed context (source display name, extension
format, content format). Counted on `FormatInfo.diagnostics`; under
`COLLECT`/`RAISE` + budget, retained; logged via `archivey.detection` per policy.
SHALL NOT attach to `ArchiveInfo`. If a reader is created, the occurrence already
belongs to the transferred collector.

#### Scenario: conflict matrix

| Case | Expected |
| --- | --- |
| `archive.tar.gz` with 7z magic | `SEVEN_Z` + `FORMAT_EXTENSION_CONFLICT` on `FormatInfo`; default policy logs on `archivey.detection` |
| `open_archive` policy raises on conflict | `DiagnosticRaisedError` during detection; no reader |
| `open_archive(..., format=ZIP)` | No format-conflict diagnostic |

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

### Requirement: Magic-byte table

Exact matches only (no fuzzy/weak magic). Recognised:

| Format | Signature (summary) |
| --- | --- |
| ZIP | `50 4B 03 04` / `07 08` / `05 06` |
| GZip | `1F 8B` |
| BZip2 | `42 5A 68` |
| XZ | `FD 37 7A 58 5A 00` |
| Zstandard | `28 B5 2F FD` |
| 7-Zip | `37 7A BC AF 27 1C` |
| RAR 4.x / 5.x | `52 61 72 21 1A 07 00` / `… 01 00` |
| ISO 9660 | `CD001` at 32769 |
| TAR | `ustar` at 257 |
| LZ4 | `04 22 4D 18` |
| lzip | `LZIP` |
| unix-compress | `1F 9D` |

Formats without reliable exact magic (notably **zlib**) SHALL NOT appear here —
content probe only.

#### Scenario: magic matrix

| Case | Expected |
| --- | --- |
| Starts `50 4B 03 04` | ZIP, `CERTAIN`, `magic` |
| Magic table consulted for zlib | No zlib entry; CMF/FLG → zlib probe |
| `ustar` at 257, ≥512 bytes | TAR, `CERTAIN`, `magic` |

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

### Requirement: Compressed streams are probed for an inner TAR

For single-file compressors (gzip, bzip2, xz, zstd, lz4, lzip, LZMA Alone, zlib,
brotli, unix-compress), detection SHALL decompress a bounded amount of *content*
and look for TAR `ustar` at offset 257, reporting combined formats (`TAR_GZ`, …)
when present. Need ≥512 decompressed bytes.

Compressed input is supplied via a **bounded, non-consuming view** (up to
`_INNER_TAR_MAX_PROBE_BYTES`, ≥ largest bzip2 first-block compressed size):

- Stream codecs pull incrementally (first few KiB usually enough).
- Block-transform (bzip2) may pull a full first block before any output.

Seekable: read + restore position. Path: open/close. Non-seekable: buffer in
`PeekableStream` for replay. Use sequential decompression (not random-access
accelerators that reject bounded non-seekable views). Missing decompressor → bare
compressor format; open may refine. No TAR header within the bound → bare
compressor.

#### Scenario: inner-TAR matrix

| Case | Expected |
| --- | --- |
| `.gz` → content with `ustar`@257 | `TAR_GZ` (not bare `GZIP`) |
| `.gz` → non-TAR content | `GZIP` |
| `.tar.bz2` with large first block (> peek prefix) | Read up to max block; `TAR_BZ2` |
| Large-block bare `.bz2`, no `ustar` | Bounded read; `BZ2` (no false promotion) |
| Non-seekable `.tar.bz2` needing full block | Buffered in `PeekableStream`; `TAR_BZ2`; backend can still read all |
| Alone `.tar.lzma` / Alone `.tlz` with `ustar`@257 | `ArchiveFormat(TAR, LZMA_ALONE)` |
| Bare Alone `.lzma`, no `ustar` | `ArchiveFormat.LZMA_ALONE` |

### Requirement: Keep `.tlz` as TAR × LZIP; Alone content still wins

The system SHALL keep the TAR short alias `.tlz` mapped to
`ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP)` (same family as `.lz` /
`.tar.lz`). Canonical Alone paths remain `.lzma` / `.tar.lzma`. Content detection
SHALL still win over the extension alias:

| Leading bytes | Detected format |
| --- | --- |
| Exact `LZIP` magic | LZIP (then inner-TAR probe may yield TAR × LZIP) |
| Alone content probe match | LZMA Alone (then inner-TAR probe may yield TAR × LZMA_ALONE) |

A `.tlz` whose content is LZMA Alone SHALL detect as TAR × LZMA_ALONE and emit
`FORMAT_EXTENSION_CONFLICT` against the lzip alias. A `.tlz` whose content is
lzip SHALL detect as TAR × LZIP with no Alone claim.

#### Scenario: `.tlz` / Alone extension matrix

| Case | Expected |
| --- | --- |
| `test_compat_lzip_1.tlz` (`LZIP` magic + TAR) | TAR × LZIP; no Alone claim |
| `test_compat_lzma_*.tlz` (Alone payload + TAR) | `ArchiveFormat(TAR, LZMA_ALONE)`; members readable; `FORMAT_EXTENSION_CONFLICT` retained under default budget |
| Extension-only `.tlz` with unreadable/empty content | GUESS `ArchiveFormat(TAR, LZIP)` |
| Bare `.lzma` Alone, no TAR | `ArchiveFormat.LZMA_ALONE` |
| `.tar.lzma` Alone + TAR | `ArchiveFormat(TAR, LZMA_ALONE)` |

### Requirement: Self-extracting (SFX) archives are detected behind an executable stub

SFX RAR/7z/ZIP: EXE stub precedes payload. If leading bytes look like executable
(`MZ` / ELF) rather than archive magic, the system SHALL scan for RAR
(`52 61 72 21 1A 07`), 7z (`37 7A BC AF 27 1C`), or ZIP local-header
(`50 4B 03 04`) magic within a bounded forward window equal to the shared
`SFX_MAX` constant (today 2 MiB; same binding as the RAR and 7z SFX scanners)
before running content probes. Match → embedded format
with `payload_offset` = payload start and `detected_by` indicating an SFX scan.
No match → fall through per the differentiation policy from the sibling
requirement, which is graded by cue strength: a strong cue suppresses the content
probes, a weak one does not. "No silent fabricated member" is therefore guaranteed for
a **strong** cue only — see that requirement for the measured reason.

Which magic the scan hunts for SHALL be **backend-declared data**, like every other
detection signal, rather than a table maintained inside the detector. A backend declares
only the magic that can legitimately begin an appended payload: ZIP declares its
local-file header and NOT the end-of-central-directory or spanned markers, which as
needles inside a 2 MiB stub window would claim any executable containing those four
bytes.

Native RAR/7z parsers SHALL accept a start offset (read in place, no copy). ZIP
already locates the EOCD from the tail, so a leading stub is tolerated without a
separate parser scan; detection still SHALL return `ZIP` with `payload_offset`
rather than a stream codec when the ZIP needle matches.

The scan takes the **earliest** matching needle in the window. A stub that itself
contains one of these magics therefore decides `payload_offset`, and the backend opens
there rather than at a later real payload. That is a **loud** failure, not a silent one
— 7z raises `CorruptionError` on the signature CRC — and is accepted for now;
validating a hit and resuming the scan past a rejected one is deliberately out of scope
(see the change's design).

#### Scenario: SFX matrix

| Case | Expected |
| --- | --- |
| `MZ` + 7z magic at offset N | `SEVEN_Z`, `payload_offset == N`; backend opens at N |
| `MZ` + RAR magic at offset N | `RAR`, `payload_offset == N`; backend opens at N |
| `MZ` + ZIP local magic (`PK\x03\x04`) at offset N | `ZIP`, `payload_offset == N`; real ZIP members listed |
| `MZ` + low-entropy filler + RAR/7z/ZIP magic in window | Same as above — **not** `BROTLI` / fabricated single-file member |
| **Strong** executable cue (validated PE / ELF), no RAR/7z/ZIP in window | No content probe runs; extension guess or `FormatDetectionError` — never a fabricated member |
| **Weak** executable cue (bare `MZ` / `\x7fELF`), no RAR/7z/ZIP in window | Content probes run unchanged, so a probe may still claim the stub — the accepted residual, per the sibling requirement and `open-issues.md` P12 |
| Stub containing a decoy needle before the real payload | Earliest match wins; the backend opens at the decoy and fails **loudly** (7z: `CorruptionError`). ZIP usually still succeeds via EOCD-from-tail |
| Bare brotli / non-executable stream | Unchanged content-probe behaviour |

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

### Requirement: ISO 9660 requires an extended peek window

The system SHALL raise the peek window to 32774 bytes when `.iso` or ISO
detection is attempted (PVD at 32769). A stream shorter than that SHALL rule out
ISO and continue (other magic / extension) — never reject solely for being too
short for ISO. Long enough but no `CD001`@32769 → no ISO match, fall through.
`FormatDetectionError` only when **no** format matches.

#### Scenario: ISO peek matrix

| Case | Expected |
| --- | --- |
| `.iso`, ≥32774 bytes, `CD001`@32769 | ISO, `CERTAIN`, `magic` |
| Stream < 32774 bytes | ISO ruled out; fall through; error only if nothing else matches |
| 2 KiB file with ZIP magic | ZIP (despite short of ISO window) |

### Requirement: Detection never consumes or discards bytes

Bytes inspected during detection MUST remain available to the backend. Wrapping
non-seekable sources is the **opener's** job so one wrapper is shared:

| Source | Behavior |
| --- | --- |
| Path / seekable stream | Peek/read then restore entry `tell()`. Archive begins where the caller positioned. `open_archive` may wrap a mid-file seekable stream in a zero-origin view (`SlicingStream`) so absolute-offset backends (e.g. ISO/`pycdlib`) see origin 0. |
| Non-seekable | `open_archive` wraps in `PeekableStream` **before** detection and passes the **same** wrapper to detection and backend. Detection uses `peek(n)` only. |

Standalone `detect_format` is non-consuming for paths/seekable streams. For a raw
non-seekable stream the caller must pass a `PeekableStream` (or equivalent) if it
will keep reading — otherwise the peeked prefix is lost. `open_archive` wraps
internally.

`PeekableStream`: buffers first `DETECTION_LIMIT` bytes (32774 when ISO triggered);
`.peek(n)` without consume; `BinaryIO` to backend (drain buffer, then underlying).

#### Scenario: non-consuming matrix

| Case | Expected |
| --- | --- |
| Seekable `BinaryIO` at position N | After detect, position is N again; backend can read full archive |
| `open_archive` on non-seekable | One `PeekableStream` for detect + backend; peeked bytes replay then fall through |
| Standalone detect on raw non-seekable the caller will reread | Caller must supply `PeekableStream` |

### Requirement: An unconfirmed format choice is reported when the listing is empty

`detected_by="extension"` means every content signal declined: magic, the content
probes, and far magic all failed, and the extension was the only evidence left. That is
the same answer content detection gives when it **refuses** a file — so a format chosen
this way is not confirmed by the bytes, and no second detection pass is needed to know
it.

On its own that is fine and common (an empty `.br` with the Brotli extra missing, a
`.tlz` whose content is unreadable). Combined with a **listing that completes with zero
members**, it is the realistic form of the wrong-format problem: 32 KiB of zeros named
`z.tar` opens as an empty TAR, while `detect_format()` on the same bytes raises
`FormatDetectionError`.

When a listing completes without error and with zero members, the system SHALL therefore
emit `EXTENSION_FORMAT_UNCONFIRMED` if the format came from the extension fallback, and
SHALL NOT emit it otherwise. It SHALL NOT refuse the open: a zero-member archive is legal
and a zero-filled file is byte-identical to an empty one (see `diagnostics`).

The check SHALL cost nothing on a non-empty listing — it is a comparison of the recorded
`FormatInfo`, not a rescan.

#### Scenario: unconfirmed format matrix

| Case | Expected |
| --- | --- |
| 32 KiB of zeros named `z.tar` | Opens as TAR, 0 members, `EXTENSION_FORMAT_UNCONFIRMED` (+ `EMPTY_ARCHIVE`) |
| `detect_format()` on the same bytes with no name | `FormatDetectionError` — unchanged |
| Real one-member tar named `a.tar`, no magic window match | Non-empty listing → no diagnostic |
| A legitimately empty tar (all zeros, hence extension-only) | `EXTENSION_FORMAT_UNCONFIRMED` too — the bytes genuinely did not confirm it, and that is the honest answer, not a false positive |
| Empty archive opened with an explicit `format=` | `EXPLICIT_FORMAT_LISTED_EMPTY` instead — no extension fallback ran |

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
| OLE/CFB file (`D0 CF 11 E0 A1 B1 1A E1`, ≥ 7425 bytes) | Brotli first-block gate / `BrotliCodec.content_probe` still accept (MLEN 7422 always fits). End-to-end `detect_format` today claims **LZMA Alone at `PROBABLE`** (Alone wins probe order) — not a Brotli residual at the detection layer |
| COFF-shaped prefix (`64 86 …` with a fitting uncompressed trailer) | Same split: Brotli gate accepts; end-to-end Alone at `PROBABLE` |
| A 13-byte text file, LZMA Alone probe | **Rejected** — a source that is only the 13-byte header cannot be an Alone stream (removes the entire measured real-world Alone residual, 4 of 4) |
| Non-seekable source of unknown length (≥ `DETECTION_LIMIT` peek) | Gate skipped; today's behaviour |
| Non-seekable source shorter than the detection peek | Length inferred from the short peek; gate applies |
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
