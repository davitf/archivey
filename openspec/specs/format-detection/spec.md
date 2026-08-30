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
2. **Near magic** — the magic-byte table at exact offsets within that window. Match →
   `CERTAIN` / `detected_by="magic"`.
3. **Prefixed payload** — the tiers owned by *Self-extracting (SFX) archives are detected
   behind an executable stub*.
4. **Far magic** — signatures whose end offset lies outside the default window, today ISO
   9660's `CD001` at 32 769. Match → `CERTAIN` / `detected_by="magic"`. This SHALL be
   attempted **before** the content probes: it is exact magic at a known offset and they
   are the weakest signal available. It SHALL be skipped when the source size is known to
   be smaller than the extended window, and a source too short for it SHALL fall through
   rather than be rejected.
5. **Content probes** — formats with no exact magic. Match → `detected_by="content_probe"`.
6. **Extension** — `Path` with a known extension → `GUESS` / `detected_by="extension"`.
7. `FormatDetectionError` when nothing matched.

Steps are ordered attempts, not alternatives: a step that produces no match falls through,
and attempting one never prevents a later one from running.

#### Scenario: unrecognised bytes, no path

| Case | Expected |
| --- | --- |
| Non-seekable `BinaryIO`, no filename, no magic | `FormatDetectionError` |

#### Scenario: far magic precedes the content probes

| Case | Expected |
| --- | --- |
| Bootable/hybrid ISO whose 32 KiB system area holds boot code a probe accepts | `ISO` / `CERTAIN` / `magic` — not a fabricated single-file member |
| ISO with a zeroed system area | `ISO` / `CERTAIN` / `magic`; unchanged |
| Source smaller than the extended window, size known | Step 4 skipped without an extended peek; falls through |
| Source too short for the window, size unknown | Short peek, no match, falls through — never an error for being short |
| Real Brotli stream larger than the window, no extension | One bounded peek misses at step 4, then step 5 detects it |

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
| Zstandard | `28 B5 2F FD`, optionally preceded by skippable frames |
| 7-Zip | `37 7A BC AF 27 1C` |
| RAR 4.x / 5.x | `52 61 72 21 1A 07 00` / `… 01 00` |
| ISO 9660 | `CD001` at 32769 |
| TAR | `ustar` at 257 |
| LZ4 | `04 22 4D 18` |
| lzip | `LZIP` |
| unix-compress | `1F 9D` |

Formats without reliable exact magic (notably **zlib**) SHALL NOT appear here —
content probe only.

A zstd **skippable frame** — magic in `0x184D2A50 .. 0x184D2A5F` followed by a
little-endian `uint32` payload size — carries no compressed data and MAY precede the first
regular frame. Detection SHALL walk consecutive skippable frames by their declared sizes
within the peeked prefix and match the regular frame that follows. A source of skippable
frames alone, or one whose declared size runs past the prefix, SHALL NOT be claimed as
zstd: the walk is arithmetic over already-peeked bytes and never extends the read.

#### Scenario: magic matrix

| Case | Expected |
| --- | --- |
| Starts `50 4B 03 04` | ZIP, `CERTAIN`, `magic` |
| Magic table consulted for zlib | No zlib entry; CMF/FLG → zlib probe |
| `ustar` at 257, ≥512 bytes | TAR, `CERTAIN`, `magic` |

#### Scenario: zstd frame prefix

| Case | Expected |
| --- | --- |
| Regular frame at offset 0 | `ZST`, `CERTAIN`, `magic` |
| One skippable frame, then a regular frame | `ZST`, `CERTAIN`, `magic` |
| Several chained skippable frames, then a regular frame | `ZST`, `CERTAIN`, `magic` |
| Skippable frames only, no regular frame | No zstd claim; falls through |
| Skippable frame whose declared size runs past the peeked prefix | No zstd claim; no extended read |

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
positive comes from, and takes `GUESS`. This split grades evidence strength only —
`format_unconfirmed` / `PROBE_FORMAT_UNCONFIRMED` key on probe-only provenance, not on
confidence (see *Detection confidence SHALL NOT be the trigger for error provenance*).
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
| OLE/CFB file (`D0 CF 11 E0 A1 B1 1A E1`, ≥ 7425 bytes) | Brotli first-block gate / `BrotliCodec.content_probe` still accept (MLEN 7422 always fits). End-to-end `detect_format` today claims **LZMA Alone at `PROBABLE`** (Alone wins probe order) — not a Brotli residual at the detection layer |
| COFF-shaped prefix (`64 86 …` with a fitting uncompressed trailer) | Same split: Brotli gate accepts; end-to-end Alone at `PROBABLE` |
| A 13-byte text file, LZMA Alone probe | **Rejected** — a source that is only the 13-byte header cannot be an Alone stream (removes the entire measured real-world Alone residual, 4 of 4) |
| Non-seekable source of unknown length (≥ `DETECTION_LIMIT` peek) | Gate skipped; today's behaviour |
| Non-seekable source shorter than the detection peek | Length inferred from the short peek; gate applies |
| Source length known to be shorter than the declared metadata skip | Rejected |

### Requirement: A content probe SHALL NOT accept an incomplete stream it can see whole

A content probe decodes a bounded prefix and treats "the decoder wants more input" as a
match, because a real stream usually continues past the prefix. When the probe knows the
source length and that length does not exceed the bytes it was handed, there is no more
input: the probe is holding the entire file. A **complete valid stream that finishes within
a declared output drain terminates**, so a decode that still wants more input after that
bounded drain SHALL be a rejection rather than a match.

This is a **bounded** completeness check, not a full drain of the decoded output: the
implementation SHALL declare an output budget (today: 64 KiB) so a highly-compressible
fully-visible sample cannot expand into an unbounded decode. Streams whose expansion
exceeds that budget without the decoder signalling "needs more input" are not rejected
here. The check MUST NOT be implemented as a minimum source size. A 9-byte
`brotli.compress(b"hello")` finishes within the drain and SHALL still be accepted.

The rule SHALL apply to every probe that decodes, not to Brotli alone: it follows from
bounded decoding rather than from any one format's framing. Measured on 66 361 real files
(at the then-256-byte completeness drain; the drain is now 64 KiB, which only strengthens
the rule), it rejects **91 of 128** fabricated probe claims (71%) — 67 of them under
16 bytes — while costing **zero** genuine streams.

The check SHALL be skipped when the source length is unknown, exactly as the framing gate
is; detection then behaves as before.

#### Scenario: completeness matrix

| Case | Expected |
| --- | --- |
| 9-byte real Brotli stream, whole file in the prefix, decodes to completion | Accepted |
| 5-byte text file whose first meta-block parses as compressed, decode wants more input within the output drain | **Rejected** — the file is fully visible and does not terminate |
| Truncated high-ratio zlib (fully visible; expands past the old 256-byte probe read, within ~64 KiB) | **Rejected** within the declared output drain |
| Truncated high-ratio zlib whose expansion exceeds the output drain before signalling truncation | Not rejected here — bounded check cannot disprove |
| Real Brotli file larger than the prefix, decode wants more input | Accepted — there genuinely is more input |
| Real Brotli file exactly the size of the prefix, decodes to completion | Accepted |
| Source length unknown (non-seekable stream longer than the peek) | Rule skipped; today's behaviour |
| LZMA Alone: 51-byte low-entropy file, fully visible, does not terminate | **Rejected** — same rule, not a Brotli special case |
| Any source larger than the prefix | Rule does not apply; other rules decide |

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

Following the chain requires bytes at offsets that may lie past the peeked prefix. The
mechanism by which a probe reaches them is settled in this change's design; whatever it
is, the reads SHALL stay within the declared bounds and SHALL NOT decompress.

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
| OLE/CFB file ≥ 7425 bytes | Still accepted — its constant magic yields a fitting chain. Known residual, unchanged |

### Requirement: A read failure on probe-only evidence names its provenance

When a single-file result's format came from a content probe with **no corroborating
evidence** (matching extension or inner-TAR upgrade) — at any `DetectionConfidence` — a
decode failure while reading the fabricated single member SHALL report that the format
identification was unconfirmed, rather than presenting as a plain truncation of a file
whose format is settled. See `error-handling` for the attribute, message, and diagnostic
contract.

The system SHALL NOT refuse the open on this basis — a genuine extensionless stream that
the probe correctly identified must still be readable, and a probe-only result that reads
cleanly is a success.

#### Scenario: unconfirmed-format read failure

| Case | Expected |
| --- | --- |
| Probe-only result at any confidence, read fails | Same exception type; `format_unconfirmed=True`; message names unconfirmed identification; `PROBE_FORMAT_UNCONFIRMED` diagnostic |
| Probe + `.br` extension, read fails | Ordinary truncation/corruption error — the format is corroborated; `format_unconfirmed=False` |
| Probe + `.tar.br` extension reported as bare `BROTLI`, read fails | Corroborated — the deferred inner-TAR case is agreement, not conflict; `format_unconfirmed=False` |
| Probe + an extension that **disagrees** (`.zip` over Brotli bytes), read fails | Not corroborated; `format_unconfirmed=True`, and a `FORMAT_EXTENSION_CONFLICT` is raised |
| Probe hit upgraded to `TAR_*` via inner-TAR, read fails | Corroborated; `format_unconfirmed=False` |
| Probe-only result, read succeeds | Success; no error, no downgrade |

### Requirement: An inner-TAR upgrade corroborates a content-probe identification

When a content-probe hit is upgraded to a `TAR_*` format because a TAR header was found in
the decompressed prefix (`_resolve_single_file_or_tar`), that upgrade SHALL count as
corroborating evidence for the underlying codec identification, equivalent to a matching
file extension.

The upgrade is not a second guess about the same bytes: reaching it required the probe's
decompression to actually produce output, and that output to contain a `ustar` signature
at the offset TAR specifies. Two independent things had to hold. A result reached that way
SHALL therefore report `PROBABLE` rather than `GUESS`, and SHALL NOT be stamped
`format_unconfirmed` on a later decode failure (see `error-handling`).

The population is small — a `.tar.br` with no filename to go on — but the alternative is
treating a stream that decompressed successfully into a recognisable TAR as no better
evidenced than four bytes that happened to parse as a block header.

#### Scenario: inner-TAR corroboration matrix

| Case | Expected |
| --- | --- |
| Extensionless stream, Brotli probe hits, decompressed prefix contains a TAR header | `TAR_BROTLI`, `PROBABLE`, `content_probe` — corroborated |
| Same, and a later read fails | Ordinary error; `format_unconfirmed is False` |
| Extensionless stream, Brotli probe hits, no TAR header in the decompressed prefix | Unchanged — the probe-only rules decide |
| `x.tar.br` (extension already corroborates) | Unchanged |

### Requirement: Detection confidence SHALL NOT be the trigger for error provenance

`DetectionConfidence` grades how strong the evidence for a format was.  Whether a caller
is told the identification may have been wrong is a **different** question — whether
anything corroborated a probe. The system SHALL keep these separate: no error-reporting
behaviour SHALL key on a `DetectionConfidence` value.

This exists because conflating them produced a measured blind spot and then bent a
detection decision around it. `format_unconfirmed` originally fired on
`detected_by == "content_probe" and confidence is GUESS`, which left 68 of 128 real-world
fabricated probe claims unsignalled: LZMA Alone reports `PROBABLE` unconditionally, and
Brotli's compressed-first class was moved to `PROBABLE` *in order to* route it away from
the flag. A confidence value chosen to steer exception behaviour is no longer reporting
confidence.

Detection MAY still grade probe-only hits by class where it has measured grounds to — the
compressed-first split stands on its own evidence — but the grade SHALL be a claim about
evidence strength only, with no error-reporting consequence attached.

#### Scenario: separation matrix

| Case | Expected |
| --- | --- |
| Probe-only hit at `PROBABLE`, read fails | Stamped — the stamp asks about corroboration, not confidence |
| Probe-only hit at `GUESS`, read fails | Stamped — same reason |
| Corroborated hit at `PROBABLE`, read fails | Not stamped |
| A future retune of Brotli's confidence split | Changes reported confidence only; no error behaviour moves |
