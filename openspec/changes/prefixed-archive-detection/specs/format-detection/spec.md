## MODIFIED Requirements

### Requirement: Self-extracting (SFX) archives are detected behind an executable stub

An archive MAY begin after byte zero. The system SHALL look for that in **cost tiers**,
because what it costs to find depends on the format, not on how hard we are willing to
look. Detection SHALL try, in order:

1. **Exact magic at offset 0** — unchanged.
2. **Tail probe for self-locating containers** (see the ZIP requirement in `format-zip`).
   Runs whenever the source is seekable, with no cue, because the cost is bounded by the
   format rather than by a constant we chose.
3. **Prefix-cued forward scan** for containers that cannot locate themselves: RAR
   (`52 61 72 21 1A 07`), 7z (`37 7A BC AF 27 1C`), and TAR's `ustar`. Runs only when the
   leading bytes look like a *prefix* — `MZ`, `\x7fELF`, a Mach-O magic, or a `#!` shebang
   — within the shared `SFX_MAX` window.
4. **Exhaustive scan**, only when the caller opts in (`archive-reading`).

A match SHALL report the embedded format with `payload_offset` = payload start and a
`detected_by` naming which tier found it. No match SHALL fall through to content probes,
extension, then `FormatDetectionError`. Native RAR/7z parsers SHALL accept a start offset
(read in place, no copy).

The cue at tier 3 is a **cost gate, not a correctness gate**: its purpose is to avoid
reading up to `SFX_MAX` from every source, not to prevent false matches — tier 3
validation (below) does that. Widening the cue is therefore a cost decision, and MUST NOT
be justified by, or traded against, false-positive rate.

#### Scenario: SFX matrix

| Case | Expected |
| --- | --- |
| `MZ` + 7z magic at offset N | `SEVEN_Z`, `payload_offset == N`; backend opens at N |
| `\x7fELF` + RAR magic at offset N (a `rar a -sfx` stub) | `RAR`, `payload_offset == N` |
| Mach-O magic + 7z magic at offset N | `SEVEN_Z`, `payload_offset == N` — the cue covers Mach-O |
| `#!/bin/sh` + tar.gz (a makeself `.run`) | `TAR_GZ`, `payload_offset` at the gzip magic |
| Executable header, no archive magic in window | No match; content probe, extension, or `FormatDetectionError` |
| Prefix + ZIP | Found at tier 2 by the tail probe, not by the scan |
| Archive magic beyond `SFX_MAX`, caller did not opt in | No match; `FormatDetectionError` rather than an unbounded read |

## ADDED Requirements

### Requirement: A prefixed-container match is confirmed structurally, not by magic alone

A forward-scan hit SHALL be validated before it is reported, using evidence the candidate
carries about itself:

- **7z**: the 32-byte signature header self-checks. `StartHeaderCRC` is a CRC32 over the
  20-byte StartHeader that follows it, and `offset + 32 + NextHeaderOffset +
  NextHeaderSize` SHALL fall at or before the end of the source — landing exactly at the
  end is the strong form, and SHALL be preferred when several candidates validate. A
  trailing-data tolerance is required because some stubs append configuration after the
  archive.
- **RAR 5**: the 8-byte marker SHALL be followed by a main archive header whose CRC32
  matches.
- **RAR 4**: the 7-byte marker block SHALL be followed by a parseable main header.

A candidate that fails validation SHALL NOT be reported, and the scan SHALL continue.
Validation exists so that a hit can be reported at high confidence and so a scan cannot
claim a file that merely contains the magic bytes; it does **not** license scanning more
sources than the cost gate allows.

#### Scenario: scan validation matrix

| Case | Expected |
| --- | --- |
| Stub + real 7z, CRC and end-offset agree | `SEVEN_Z` at that offset, `CERTAIN` |
| The 6 magic bytes appear in unrelated data | CRC fails; not reported; scan continues |
| 7z whose declared end overruns the source | Not reported |
| 7z with trailing bytes appended after the archive | Still reported — declared end within the source |
| Stub + RAR5 whose main-header CRC fails | Not reported |

### Requirement: Detection reports what precedes the payload

When `payload_offset > 0`, `FormatInfo` SHALL carry a `prefix_kind` describing what sits in
front, so a caller can distinguish an archive meant to be extracted from an archive that
merely happens to be embedded. archivey SHALL report what it observed and SHALL NOT infer
the producer's intent beyond that.

| `prefix_kind` | meaning |
| --- | --- |
| `NONE` | `payload_offset == 0` |
| `EXECUTABLE` | PE, ELF or Mach-O — a self-extracting archive |
| `SCRIPT` | a `#!` shebang — a self-extracting shell installer |
| `OTHER_FORMAT` | the prefix is itself a recognised format (e.g. an image) — an embedded or polyglot file |
| `UNKNOWN` | a prefix that matched no cue, reachable only via the opt-in exhaustive scan |

#### Scenario: prefix kinds

| Case | Expected |
| --- | --- |
| Plain `.zip` | `prefix_kind == NONE` |
| `rar a -sfx` output | `EXECUTABLE` |
| `zipapp` `.pyz`, Spring Boot executable JAR | `SCRIPT` |
| JPEG with an appended ZIP | `OTHER_FORMAT` |
| ZIP found by the opt-in scan behind unrecognised bytes | `UNKNOWN` |
