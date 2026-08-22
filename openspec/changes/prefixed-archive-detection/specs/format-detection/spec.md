## MODIFIED Requirements

### Requirement: Self-extracting (SFX) archives are detected behind an executable stub

An archive MAY begin after byte zero. The system SHALL look for that in **cost tiers**,
because what it costs to find depends on the format, not on how hard we are willing to
look. Detection SHALL try, in order:

1. **Exact magic at offset 0** — unchanged.
2. **Tail probe for self-locating containers** (see the ZIP requirement in `format-zip`).
   Runs whenever the source is seekable, with no cue, because the cost is bounded by the
   format rather than by a constant we chose.
3. **Prefix-cued forward scan** for containers that cannot locate themselves, within the
   shared `SFX_MAX` window (today 2 MiB; same binding as the RAR and 7z SFX scanners), run
   before content probes. Needles: RAR (`52 61 72 21 1A 07`), 7z (`37 7A BC AF 27 1C`),
   TAR's `ustar`, and ZIP's local-file header (`50 4B 03 04`).
4. **Exhaustive scan**, only when the caller opts in (`archive-reading`).

Which magic tier 3 hunts for SHALL remain **backend-declared data**, not a table inside the
detector, and a backend SHALL declare only magic that can legitimately *begin* an appended
payload — ZIP declares its local-file header and NOT the end-of-central-directory or
spanned markers, which as needles inside a 2 MiB window would claim any executable
containing those four bytes.

ZIP keeps its tier-3 needle even though tier 2 now finds prefixed ZIPs more cheaply,
because tier 2 needs a seekable source and tier 3 does not.

The tier-3 cue SHALL fire on `MZ`, `\x7fELF`, **any Mach-O magic**, or a **`#!` shebang**.
The weak/strong grading is retained: a strong cue (a validated PE, a valid ELF ident block,
or a Mach-O whose `cputype`/`filetype` parse) suppresses the content probes; a weak one
does not.

A match SHALL report the embedded format with `payload_offset` = payload start and a
`detected_by` naming which tier found it. No match SHALL fall through to content probes,
extension, then `FormatDetectionError`. Native RAR/7z parsers SHALL accept a start offset
(read in place, no copy). A prefixed ZIP SHALL be reported as `ZIP` with a `payload_offset`
— never as a stream codec — whichever tier finds it; ZIP needs no separate parser scan,
since the reader already locates the central directory from the tail.

The cue at tier 3 is a **cost gate, not a correctness gate**: its purpose is to avoid
reading up to `SFX_MAX` from every source, not to prevent false matches — the validation
requirement below does that. Widening the cue is therefore a cost decision, and MUST NOT
be justified by, or traded against, false-positive rate.

**Earliest-match is replaced by earliest-*valid*-match.** The superseded requirement took
the earliest matching needle and accepted that a decoy inside the stub would send the
backend to the wrong offset, failing loudly; it deliberately deferred validating a hit and
resuming the scan. The validation requirement below closes that: a candidate that fails
its structural check SHALL NOT be reported and the scan SHALL continue past it.

#### Scenario: SFX matrix

| Case | Expected |
| --- | --- |
| `MZ` + 7z magic at offset N | `SEVEN_Z`, `payload_offset == N`; backend opens at N |
| `MZ` + RAR magic at offset N | `RAR`, `payload_offset == N` |
| `\x7fELF` + RAR magic at offset N (a `rar a -sfx` stub) | `RAR`, `payload_offset == N` |
| **Mach-O (thin 64-bit) + 7z magic at offset N** | `SEVEN_Z`, `payload_offset == N` — previously `BROTLI` with a fabricated member |
| `#!/bin/sh` + tar.gz (a makeself `.run`) | `TAR_GZ`, `payload_offset` at the gzip magic |
| `MZ` + ZIP local magic at offset N, non-seekable source | `ZIP`, `payload_offset == N` — tier 3, since tier 2 needs a seek |
| Prefix + ZIP, seekable source | Found at tier 2 by the tail probe, not by the scan |
| **Strong** cue, no needle in window | No content probe runs; extension guess or `FormatDetectionError` |
| **Weak** cue, no needle in window | Content probes run unchanged |
| Stub containing a decoy needle before the real payload | Decoy fails validation; scan resumes and finds the real payload |
| Archive magic beyond `SFX_MAX`, caller did not opt in | No match; `FormatDetectionError` rather than an unbounded read |
| Bare brotli / non-executable stream | Unchanged content-probe behaviour |

### Requirement: Executable-looking prefixes must not silently become a wrong stream format

When a source’s leading bytes look executable-shaped, detection SHALL NOT let a
content probe (notably Brotli) claim a stream codec and allow `open_archive` to
succeed with a fabricated single-file member (e.g. `*.uncompressed`). That is a
silent wrong answer.

This obligation is **outcome-shaped**, not “disable Brotli whenever the prefix is
`MZ`”. A genuine Brotli (or other probe-matched) stream whose first bytes happen
to look executable MUST remain detectable.

The rule, settled by measurement in `sfx-format-detection`'s design (now archived) and
extended by this change's, grades the evidence:

- A **weak** cue — a bare `MZ`, `\x7fELF`, Mach-O magic, or `#!` prefix — SHALL trigger the
  forward scan and nothing else. When the scan finds no archive magic, content probes run
  unchanged. Two or four bytes are not proof, and refusing a probe on them would reject real
  streams.
- A **strong** cue — a DOS header whose `e_lfanew` points at a `PE\0\0` signature, an
  ELF identification block with valid `EI_CLASS` / `EI_DATA` / `EI_VERSION`, or a Mach-O
  header whose `cputype` and `filetype` parse — with no archive magic in the window SHALL
  suppress content probes entirely; detection falls through to the extension guess or
  `FormatDetectionError`. A structurally confirmed executable is not a compressed stream.

The set of prefixes that raise a cue at all is a **cost** decision, governed by the sibling
requirement; the weak/strong grading above is what decides the *outcome* once a cue exists.
The two are independent: widening the cue set does not weaken this rule, and this rule does
not license reading `SFX_MAX` from sources the cost gate excludes.

**A prefix outside the cue set gets neither treatment, and that is where this obligation was
being broken.** Before this change the cue recognised `MZ` and ELF only, so a thin
little-endian Mach-O stub raised no cue — while `cf fa ed fe` is *structurally guaranteed*
to parse as an uncompressed Brotli meta-block header. Measured end to end: PE and ELF stubs
in front of a 7z opened the real members, and a Mach-O stub returned `BROTLI` with one
fabricated `.uncompressed` member. Adding Mach-O to the cue set is what closes it; the
grading above was already correct.

The system SHALL NOT tighten the Brotli probe itself to satisfy this requirement:
measured, a larger probe prefix does not reduce false positives (8.27% → 8.13% of random
data at 16x the prefix) and requiring decoded output loses real streams roughly
one-for-one. The residual — arbitrary non-archive data that the Brotli probe claims,
which is a far wider problem than executable prefixes — is out of scope here and is
tracked separately (`dev-docs/open-issues.md` P12, `dev-docs/threat-model.md` O10).

#### Scenario: no silent wrong answer on executable-shaped prefix

| Case | Expected |
| --- | --- |
| Low-entropy `MZ` stub + RAR/7z/ZIP payload in window | Detected as that archive — **not** `BROTLI` / fabricated member |
| **Thin little-endian Mach-O stub + 7z payload** | `SEVEN_Z` — previously `BROTLI` (or `LZMA_ALONE`) with a fabricated member |
| `#!/bin/sh` + tar.gz in window | Detected as that archive, not claimed by a probe |
| Real Brotli stream with non-executable prefix | Unchanged — still `BROTLI` via content probe |
| Real Brotli (or other probe format) whose prefix coincides with a **weak** executable cue | Still detected as that stream — **not** forced to `FormatDetectionError` solely because two bytes were `MZ` |
| **Strong** executable cue (validated PE / ELF / Mach-O), no archive needle in the window | No content probe runs; extension guess or `FormatDetectionError` — never a fabricated member |
| Executable-shaped prefix, no archive needle, probe correctly rejects | Extension guess or `FormatDetectionError` — not a fabricated member |

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
