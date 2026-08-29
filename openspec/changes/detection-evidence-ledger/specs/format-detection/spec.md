## ADDED Requirements

### Requirement: Detection produces an evidence ledger, not a single verdict

Each candidate SHALL accumulate typed evidence records, and evidence classes SHALL be
**totally ranked**, strongest first. Evidence SHALL NOT be summed: two correlated weak
signals SHALL NOT outrank one stronger signal.

```python
class EvidenceClass(Enum):        # strongest first
    COMPLETE = "complete"
    SELF_VALIDATING = "self_validating"
    DISCRIMINATING_HEADER = "discriminating_header"
    SIGNATURE_ONLY = "signature_only"
    BOUNDED_PROBE = "bounded_probe"
    NAME = "name"
    ASSERTED = "asserted"

@dataclass(frozen=True)
class DetectionEvidence:
    kind: EvidenceKind          # MAGIC, CONTENT_PROBE, NAME, DECLARED_BY_CALLER, …
    strength: EvidenceClass
    anchor: EvidenceAnchor      # ORIGIN, CANDIDATE, END, FLOATING, NONE
    offset: int
    bytes_examined: int
    validation: ValidationState # VALID, INVALID, INCOMPLETE, NOT_APPLICABLE
    estimated_random_bits: float | None = None
    detail: str | None = None
```

| class | establishes |
| --- | --- |
| `COMPLETE` | a decoder reached stream end on the entire source, or a container parsed completely |
| `SELF_VALIDATING` | a format-specific identifier and an independent consistency check agree |
| `DISCRIMINATING_HEADER` | enough mandatory structure to make accidental identity remote |
| `SIGNATURE_ONLY` | identity evidence with no structural confirmation |
| `BOUNDED_PROBE` | a prefix decode accepted the bytes without reaching source end |
| `NAME` | a prior from the caller's namespace — not content evidence |
| `ASSERTED` | the `format=` argument, which skipped detection — an instruction, not an observation |

`EvidenceAnchor` distinguishes an offset measured from the detection origin, from *this
candidate's* payload offset, from the source end, from a scan hit with no fixed
relationship, and from non-positional evidence. `ORIGIN` and `CANDIDATE` coincide when the
payload begins at zero; keeping them distinct is what lets a prefixed archive's internal
evidence stay anchored instead of degrading to floating.

Per-record detail — `bytes_examined`, `estimated_random_bits`, anchors — is advisory. The
stable commitments are the kinds, the classes, and their ordering.

#### Scenario: evidence map for the current formats

| detector, after cheap validation | achieved class |
| --- | --- |
| gzip `CM` + reserved `FLG` (no `FHCRC`) · `.Z` header | `SIGNATURE_ONLY` |
| bzip2 header + first block marker · zstd/lzip header · ISO descriptor · validated ZIP local header | `DISCRIMINATING_HEADER` |
| XZ flags CRC · LZ4 header checksum · TAR header checksum · 7z `StartHeaderCRC` · RAR main-header CRC · gzip with a verified `FHCRC` | `SELF_VALIDATING` |
| zlib / LZMA Alone / Brotli bounded decode without exact end-of-stream | `BOUNDED_PROBE` |
| whole-source decode to end-of-stream, or a complete container parse | `COMPLETE` |
| filename only | `NAME` |

#### Scenario: evidence is not additive

| Case | Expected |
| --- | --- |
| Bounded probe plus a matching filename | Class stays `BOUNDED_PROBE`; the `NAME` record is retained in the ledger |
| Checksum-validated header versus filename plus weak decode | The validated header wins; the pair does not accumulate past it |
| Two records of the same class on one candidate | The candidate's class is the strongest single record, never a sum |

### Requirement: An incomplete validation caps a candidate at SIGNATURE_ONLY

When a declaration's structural validator cannot run to a verdict because the source holds
too few bytes, the validation state SHALL be `INCOMPLETE` and the candidate SHALL be capped
at `SIGNATURE_ONLY` regardless of the declaration's evidence ceiling. The signature matched;
nothing corroborated it.

A candidate SHALL NOT be rejected merely because the source is shorter than the format's
minimum header. Identification and completeness are different questions: a truncated `.gz`
pulled off a damaged backup SHALL report `GZ`, not "unknown format".

#### Scenario: sources too short to validate

| Case | Expected |
| --- | --- |
| Source is exactly `1f 8b` (2 bytes) | `GZ`, `SIGNATURE_ONLY` → `PROBABLE` — not `CERTAIN` |
| Source is exactly `BZh` (3 bytes) | `BZ2`, `SIGNATURE_ONLY` → `PROBABLE` |
| Source is exactly `PK\x03\x04` (4 bytes) | `ZIP`, `SIGNATURE_ONLY` → `PROBABLE` |
| Any registered magic entry, source is only that magic | `SIGNATURE_ONLY`; never `CERTAIN` |
| Truncated but valid-headed `.gz` | `GZ` identified; truncation is a read-time concern |
| Source of zero bytes, no filename | `FormatDetectionError` whose record says *capability shortfall*, not *exhausted search* |

### Requirement: A decode counts as evidence only if it decoded something

A bounded probe that succeeds having produced **only stored or uncompressed output** SHALL
be graded on its header alone and SHALL NOT reach `BOUNDED_PROBE` on the strength of that
decode. The decoder copied bytes; it learned nothing the header did not already say.

This is not a resource rule — stored blocks are one-to-one and are a tool for a false
identity, not for amplification.

#### Scenario: stored-only decodes across the magic-less formats

| format | stored mode | rule |
| --- | --- | --- |
| Brotli | uncompressed / metadata meta-blocks | already handled by the first-block framing gate |
| zlib / deflate | `BTYPE=00` stored blocks | graded on the 2-to-the-minus-10 header alone; still identified as zlib |
| LZMA Alone | none — LZMA1 is always range-coded | not applicable |

| Case | Expected |
| --- | --- |
| `zlib.compress(payload, 0)`, 200 000 bytes of stored blocks | `ZLIB` identified, graded on its header — not `PROBABLE` on the decode |
| zlib stream with at least one entropy-coded block | `BOUNDED_PROBE` |

### Requirement: Selection compares evidence and stops only when nothing can change the winner

The system SHALL select the unique maximal candidate under the evidence ranking plus ordered
priority keys, and SHALL stop acquiring evidence only when the winner is unique **and** every
unrun declaration is one of: incapable of producing a candidate that dominates it,
unavailable by source capability, or excluded by an explicit budget that the result records.

Priority keys are consulted one at a time, on a tie, and SHALL NOT be summed:

1. strongest content-evidence class;
2. semantic position — a format beginning at the detection origin outranks an unrelated
   embedded payload at a later offset;
3. end anchoring — `declared_end == source_end` before merely `<=`, within the same format
   and class;
4. a matching **filename** — it may separate two candidates equal under keys 1–3 and may do
   nothing else. Both candidates keep the same confidence and the same `format_unconfirmed`
   behaviour whichever wins;
5. still tied → ambiguous.

**Refinement is not tie-breaking.** Corroboration that changes the class or the format
replaces the candidate before selection runs and never reaches key 4: a gzip candidate whose
decoded prefix holds a checksum-valid TAR header *becomes* `TAR_GZ` rather than leaving `GZ`
and `TAR_GZ` tied.

Far fixed-offset evidence preceding the content probes SHALL follow from the ranking rather
than from a separate rule: `DISCRIMINATING_HEADER` dominates `BOUNDED_PROBE`, so a probe
result can never let the scheduler stop while a reachable far declaration is unrun.

#### Scenario: stopping rule

| Case | Expected |
| --- | --- |
| Validated XZ header plus `.xz` | May stop after the fixed-offset evidence the policy requires |
| Brotli bounded probe plus `.br` | SHALL NOT skip reachable ISO far magic or an enabled ZIP tail tier |
| gzip short header plus `.gz` | SHALL NOT skip stronger fixed-offset evidence |
| A tier skipped by explicit budget | Result records the search as incomplete; the result is budget-limited, not certain |
| A tier unavailable by capability | Recorded as unavailable, distinctly from a budget skip |
| Registry order permuted within a tier | Same winner, or `AmbiguousFormatError` — never a different answer |

#### Scenario: outcome matrix

| detection state | `detect_format()` | `open_archive()` / `open_stream()` |
| --- | --- | --- |
| Unique winner, search complete for the primary | Return `FormatInfo` | Open it |
| Unique winner, a budget skipped a detector that could tie or dominate | Report `search_complete=False`, or raise an incomplete-detection error | Refuse automatic open unless policy accepts incomplete detection |
| Tied maximal candidates | Raise `AmbiguousFormatError` carrying them | Propagate; never fall back to registry order |
| No candidate | Raise `FormatDetectionError` | Propagate |

### Requirement: Cheap structural validators grade a signature match

A magic declaration SHALL be a signature **plus an optional structural validator**. The
validator strengthens or diagnoses the match; it is not always a hard gate, and its failure
disposition is format-declared.

| format | check | disposition |
| --- | --- | --- |
| gzip | `CM == 8`; `FLG` reserved bits 5–7 zero; optional-field bounds; verify `FHCRC` when the bit is set and the CRC16 is fully available | A failed mandatory check on a two-byte signature rejects. `XFL` / `OS` SHALL NOT gate identity |
| bzip2 | `BZh`, block size ASCII `1`–`9`, then the block marker or the empty-stream end marker | Short source is `INCOMPLETE` |
| `.Z` | `1f 9d`; max-code width at least 9, supported to 16; block-mode and reserved bits | Reserved bits downrank or warn; they do not erase identity |
| XZ | six-byte magic; two stream flags; CRC32 over the flags; first flag byte zero; reserved high nibble zero | A CRC-valid but unsupported check ID is "XZ with an unsupported feature", not "not XZ" |
| zstd | regular-frame or skippable-frame magic; reserved frame-header bit zero; descriptor and present fields within bounds | |
| LZ4 | four-byte magic; `FLG` version `01`; reserved bits and `BD` legal; the one-byte xxHash header checksum | Checksum failure identifies a damaged header; it does not erase the four-byte identity |
| lzip | `LZIP`; version 1; coded dictionary size resolving to 4 KiB–512 MiB | A future version is "lzip version unsupported", not arbitrary data |
| TAR | parse the full 512-byte header; accept the stored checksum against **both** the unsigned POSIX and the historical signed sum | Replaces bare `ustar`. Permits a conservative v7 candidate without `ustar`, requiring plausible numeric, type and name fields |
| ZIP at origin | parse local/empty header fields; prefer end-of-central-directory geometry plus referenced records when the tier is enabled | `PK\x05\x06` is an empty ZIP only with a valid record; `PK\x07\x08` alone is too contextual |
| 7z | six-byte signature; version; `StartHeaderCRC` over the next 20 bytes; next-header offset and size within the known source | CRC failure after the six-byte identifier means damaged 7z, not unknown bytes |
| RAR | RAR4/RAR5 marker plus a parseable, CRC-valid main header | |
| ISO 9660 | at 32 768: type 0–3, `CD001`, version 1 | Type 255 at sector 16 cannot start a valid set. Invalid surrounding fields lower the class or indicate damage |
| zlib | RFC 1950 grammar, plus at least one entropy-coded block before the decode counts | See the stored-only requirement |
| LZMA Alone | legal properties byte; any 32-bit dictionary value; size field; then a bounded decode | |
| Brotli | RFC-valid window bits and meta-block framing; source-length overrun; completeness; bounded chain walk | Stays a bounded probe unless a complete decode reaches end-of-stream |

**v7 TAR is an anchored-only candidate and SHALL NOT be a scan needle** — it has no
identifier to generate candidates from, so in a scan it would have to be tried at every
offset.

#### Scenario: validation failure grades rather than erases

| Case | Expected |
| --- | --- |
| 7z signature, `StartHeaderCRC` mismatch | `SEVEN_Z` identified and damaged; a `CorruptionError` on open, not "unknown format" |
| gzip `FLG.FHCRC` set, CRC16 verifies | `SELF_VALIDATING` |
| gzip `FLG.FHCRC` set, CRC16 not fully available | `INCOMPLETE` — not a pass |
| gzip `FLG.FHCRC` clear | `SIGNATURE_ONLY`; gzip is one declaration, not two |
| TAR block with a checksum valid under the historical signed sum only | Accepted |
| XZ flags CRC valid, unsupported check ID | Identified as XZ |

### Requirement: Tied maximal candidates raise rather than resolve by registry order

The system SHALL raise `AmbiguousFormatError` — a `FormatDetectionError` subclass carrying
the tied candidates and their evidence — when two incompatible candidates remain maximal
after all settled priority keys. `open_archive` and `open_stream` SHALL propagate it; an
explicit `format=` continues to bypass detection.

Archivey SHALL represent multiple **archive** truths without becoming a general file-type
detector: for a JPEG with an appended ZIP it may report the ZIP payload and that the prefix
is non-archive bytes, and it need not identify JPEG.

#### Scenario: ambiguity

| Case | Expected |
| --- | --- |
| Two magic-less probes accept the same bytes at the same class | `AmbiguousFormatError` carrying both |
| An executable holding both an end-anchored ZIP and a CRC-valid 7z payload | `AmbiguousFormatError` — tier order is not evidence of producer intent |
| One candidate dominates | Returned normally; losing candidates are retained under `THOROUGH` |
| Existing `except FormatDetectionError` handlers | Continue to catch the ambiguous case |

## MODIFIED Requirements

### Requirement: Magic-first detection with extension fallback and confidence scoring

The system SHALL execute format detection as an **ordered acquisition plan with evidence-based
selection** — acquisition is ordered by what each tier can establish, and selection compares
typed evidence once every tier that could dominate has run. Acquisition order:

1. Read the name, the source capabilities, a cheap size, and the near prefix (default 4096
   bytes) through the detection prefix workspace. The name is recorded as `NAME` evidence and
   is never a gate.
2. **Near magic** and each matching declaration's structural validator.
3. **Far fixed-offset** evidence — today ISO 9660's descriptor at 32 769 — skipped only when
   the remaining source provably cannot reach the offset.
4. **ZIP tail** evidence, when the source is `TAIL`-capable and the policy enables the tier.
5. **Cue-gated bounded forward scan**, resuming one byte past a rejected candidate's **start**.
6. **Exhaustive discovery**, only under its own explicit opt-in — not a budget level, because
   its cost is not bounded by any format.
7. **Magic-less content probes** — all applicable ones, retaining every hit; a separate
   whole-source completion declaration may reach `COMPLETE`.
8. **Filename**, as corroboration and, only now, as a last-resort candidate.

The filename SHALL NOT restrict which detectors run, SHALL NOT exclude a content candidate
whose suffix disagrees, and SHALL NOT suppress a conflict diagnostic. Wrong extensions are a
founding use case.

Acquisition order is not a claim that every tier is enabled by the default budget; see
`detection-cost`. Selection, the stopping rule and the ambiguity outcome are specified by
*Selection compares evidence and stops only when nothing can change the winner*.

#### Scenario: unrecognised bytes, no path

| Case | Expected |
| --- | --- |
| Non-seekable `BinaryIO`, no filename, no magic | `FormatDetectionError` |
| Zero-byte source, no filename | `FormatDetectionError` whose record names a capability shortfall rather than an exhausted search |

#### Scenario: far magic precedes the content probes

| Case | Expected |
| --- | --- |
| Bootable/hybrid ISO whose system area holds boot code a probe accepts | `ISO` — far evidence dominates a bounded probe |
| Source smaller than the far window, size known | Far declaration skipped; recorded, not silently dropped |
| `x.br` holding a real Brotli stream | Detected by the probe, not by the extension — the extension never answers first |
| Extensionless file holding a real Brotli stream | Still detected; a missing name skips nothing |
| Non-archive `.zip`, nothing else matched | `NAME` candidate at `GUESS`, reached only because every content detector declined |

### Requirement: Magic-less formats are detected by a content probe

When no exact magic matches, the system SHALL run **every** applicable registered content
probe on the peeked prefix (consuming nothing) and retain every hit, rather than returning
from the first that accepts. This covers Brotli (no signature), zlib (too-unspecific CMF/FLG)
and LZMA Alone (a properties byte too weak for exact magic). Probes decode a bounded prefix,
MAY gate on cheap structural bytes first, and MAY consult the source length. A probe is
skipped when its backend is absent, and the skip is recorded.

A probe match SHALL report `detected_by="content_probe"` and the class `BOUNDED_PROBE`,
**for every magic-less format and regardless of the filename**. A whole-source decode that
reaches end-of-stream with no forbidden trailing bytes is a separate declaration reaching
`COMPLETE`.

The zlib probe SHALL gate on the RFC 1950 grammar — `CM == 8`, `CINFO <= 7`,
`(CMF * 256 + FLG) % 31 == 0`, `FDICT` accounted for. The LZMA Alone probe SHALL attempt a
bounded `FORMAT_ALONE` decode, SHALL NOT reject a zero dictionary size, and MUST NOT claim
streams that already matched exact magic (lzip `LZIP`, xz `FD 37 7A…`).

Brotli's compressed-versus-uncompressed first-block split is retained as **evidence detail**
in the ledger. It SHALL NOT set public confidence by itself: the random-data result does not
transfer to the measured real population, where the compressed-first class produced 64
fabricated claims against 4 genuine streams.

Before a probe result is selected: every affordable stronger tier SHALL have run; a skipped
stronger tier SHALL set `search_complete=False`; a structurally valid executable cue SHALL
prevent a raw-stream probe from winning; an inner-TAR hit refines the candidate; and a
matching extension corroborates without promoting the candidate above a stronger class.

#### Scenario: content-probe matrix

| Case | Expected |
| --- | --- |
| Bounded prefix decodes as Brotli, name is `x.br` | `BROTLI`, `BOUNDED_PROBE` → `GUESS` — the `NAME` record is retained |
| Bounded prefix decodes as Brotli, first meta-block compressed, no extension | `BROTLI`, `BOUNDED_PROBE` → `GUESS`; first-block class kept as detail |
| zlib header plus a clean decode with entropy-coded blocks | `ZLIB`, `BOUNDED_PROBE` → `GUESS` |
| LZMA Alone bounded decode | `LZMA_ALONE`, `BOUNDED_PROBE` → `GUESS` |
| Whole-source Brotli decode reaching end-of-stream under `THOROUGH` | `COMPLETE` → `CERTAIN` |
| `.br`, Brotli extra missing | Probe skipped and recorded; `NAME` candidate at `GUESS` |
| Structurally valid PE/ELF/Mach-O prefix | Raw-stream probe cannot win, even if it accepts |

#### Scenario: header grammars accept the full legal range

| Case | Expected |
| --- | --- |
| zlib stream at any window size 512 B – 32 KiB | `ZLIB` at `BOUNDED_PROBE` — all seven |
| zlib header with `FDICT` set, dictionary available | `ZLIB` at `BOUNDED_PROBE` |
| zlib header with `FDICT` set, dictionary unavailable | Decode fails; no zlib claim |
| Header failing `CM == 8`, `CINFO <= 7` or the mod-31 check | No zlib claim; no decode attempted |
| LZMA Alone stream whose dictionary-size field is zero | `LZMA_ALONE` at `BOUNDED_PROBE` |
| Zero-filled source with `CD001` at 32 769 | `ISO` from far evidence; no Alone claim |

### Requirement: An inner-TAR upgrade corroborates a content-probe identification

When a content-probe hit is upgraded to a `TAR_*` format because a checksum-valid TAR header
was found in the decompressed prefix, that upgrade SHALL be **refinement**: it replaces the
candidate rather than competing with it, and it carries the class the resulting TAR evidence
achieves.

Reaching it required the decompression to produce output and that output to hold a valid TAR
header at the offset TAR specifies — two independent things. So it is stronger **content**
evidence and may move the candidate out of the bounded-probe class, unlike a filename, which
cannot.

Because refinement runs before selection, an inner-TAR hit never reaches the filename
tie-break key: by the time keys are consulted the refinement has already decided.

#### Scenario: inner-TAR corroboration matrix

| Case | Expected |
| --- | --- |
| Extensionless stream, probe hits, decompressed prefix holds a checksum-valid TAR header | `TAR_BROTLI` as one refined candidate — not `BROTLI` and `TAR_BROTLI` tied |
| Same, class | The TAR evidence's class, above `BOUNDED_PROBE` |
| Same, later read fails | Not stamped `format_unconfirmed` — the content evidence is above the threshold |
| Probe hits, no TAR header in the decompressed prefix | Unchanged; the bounded-probe rules decide |
| `x.tar.br` where the probe could not run | Bare-compressor result plus a `NAME` record; the deferred case is agreement, not conflict |

### Requirement: Detection confidence SHALL NOT be the trigger for error provenance

`DetectionConfidence` SHALL be a **projection of the winning candidate's strongest
content-evidence class**, not a second parallel score:

| confidence | classes |
| --- | --- |
| `CERTAIN` | `COMPLETE`, `SELF_VALIDATING` |
| `PROBABLE` | `DISCRIMINATING_HEADER`, `SIGNATURE_ONLY` |
| `GUESS` | `BOUNDED_PROBE`, `NAME`, `ASSERTED` |

The rows are the classes one-for-one with no overlap. A `NAME` item SHALL NOT raise
confidence: it is not content evidence, and letting it move the scalar would recreate the
second ranking this design removes. `GUESS` therefore means *"the bytes did not confirm this
identity"*, not *"this is probably the wrong format"*.

`CERTAIN` SHALL NOT be read as "the archive is undamaged": a signature can identify a damaged
archive whose checksum fails.

No error path SHALL branch on a `DetectionConfidence` value. Error provenance asks whether the
winner was probe-only, name-only, structurally validated, or explicit — which is the class,
consulted directly.

#### Scenario: separation matrix

| Case | Expected |
| --- | --- |
| Bounded probe, no name | `GUESS` |
| Bounded probe plus a matching name | `GUESS` — unchanged by the name |
| Two-byte magic on a two-byte source | `PROBABLE` via `SIGNATURE_ONLY` |
| 7z signature with a valid `StartHeaderCRC` | `CERTAIN` |
| 7z signature with a failed `StartHeaderCRC` | Identified and damaged; confidence reflects the achieved class, and the read raises |
| Caller passed `format=` | `GUESS` via `ASSERTED`; never stamped `format_unconfirmed` |
| A future retune of a probe's grading | Changes its **class**, with measurement — not a second opinion applied at projection time |
