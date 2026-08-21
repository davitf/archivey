## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: Executable-looking prefixes must not silently become a wrong stream format

When a source’s leading bytes look executable-shaped, detection SHALL NOT let a
content probe (notably Brotli) claim a stream codec and allow `open_archive` to
succeed with a fabricated single-file member (e.g. `*.uncompressed`). That is a
silent wrong answer.

This obligation is **outcome-shaped**, not “disable Brotli whenever the prefix is
`MZ`”. A genuine Brotli (or other probe-matched) stream whose first bytes happen
to look executable MUST remain detectable.

The rule, settled by measurement in the change's design, grades the evidence:

- A **weak** cue — a bare `MZ` or `\x7fELF` prefix — SHALL trigger the SFX scan and
  nothing else. When the scan finds no archive magic, content probes run unchanged. Two
  or four bytes are not proof, and refusing a probe on them would reject real streams.
- A **strong** cue — a DOS header whose `e_lfanew` points at a `PE\0\0` signature, or an
  ELF identification block with valid `EI_CLASS` / `EI_DATA` / `EI_VERSION` — with no
  archive magic in the window SHALL suppress content probes entirely; detection falls
  through to the extension guess or `FormatDetectionError`. A structurally confirmed
  executable is not a compressed stream.

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
| Real Brotli stream with non-executable prefix | Unchanged — still `BROTLI` / `PROBABLE` via content probe |
| Real Brotli (or other probe format) whose prefix coincides with a **weak** executable cue | Still detected as that stream — **not** forced to `FormatDetectionError` solely because two bytes were `MZ` |
| **Strong** executable cue (validated PE / ELF), no archive needle in the window | No content probe runs; extension guess or `FormatDetectionError` — never a fabricated member |
| Executable-shaped prefix, no archive needle, probe correctly rejects | Extension guess or `FormatDetectionError` — not a fabricated member |
