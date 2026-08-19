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
requirement (content probes / extension / `FormatDetectionError`).

Native RAR/7z parsers SHALL accept a start offset (read in place, no copy). ZIP
already locates the EOCD from the tail, so a leading stub is tolerated without a
separate parser scan; detection still SHALL return `ZIP` with `payload_offset`
rather than a stream codec when the ZIP needle matches.

#### Scenario: SFX matrix

| Case | Expected |
| --- | --- |
| `MZ` + 7z magic at offset N | `SEVEN_Z`, `payload_offset == N`; backend opens at N |
| `MZ` + RAR magic at offset N | `RAR`, `payload_offset == N`; backend opens at N |
| `MZ` + ZIP local magic (`PK\x03\x04`) at offset N | `ZIP`, `payload_offset == N`; real ZIP members listed |
| `MZ` + low-entropy filler + RAR/7z/ZIP magic in window | Same as above — **not** `BROTLI` / fabricated single-file member |
| Executable-shaped header, no RAR/7z/ZIP in window | Per differentiation policy (see sibling requirement) — never silent fabricated member |
| Bare brotli / non-executable stream | Unchanged content-probe behaviour |

## ADDED Requirements

### Requirement: Executable-looking prefixes must not silently become a wrong stream format

When a source’s leading bytes look executable-shaped, detection SHALL NOT let a
content probe (notably Brotli) claim a stream codec and allow `open_archive` to
succeed with a fabricated single-file member (e.g. `*.uncompressed`). That is a
silent wrong answer.

This obligation is **outcome-shaped**, not “disable Brotli whenever the prefix is
`MZ`”. A genuine Brotli (or other probe-matched) stream whose first bytes happen
to look executable MUST remain detectable. The implement PR SHALL investigate how
to distinguish SFX stubs from such streams before locking the probe policy
(candidate levers: stronger PE/ELF/SFX stub cues; larger / stricter Brotli probe
than today’s `_PROBE_PREFIX = 256` + `TruncatedError`→True; SFX-scan-first then
probe only on miss; combinations). Document the chosen rule and its false-positive
/ false-negative trade-offs in the change’s design.

#### Scenario: no silent wrong answer on executable-shaped prefix

| Case | Expected |
| --- | --- |
| Low-entropy `MZ` stub + RAR/7z/ZIP payload in window | Detected as that archive — **not** `BROTLI` / fabricated member |
| Real Brotli stream with non-executable prefix | Unchanged — still `BROTLI` / `PROBABLE` via content probe |
| Real Brotli (or other probe format) whose prefix coincides with a weak executable cue | Still detected as that stream after the investigation’s rule — **not** forced to `FormatDetectionError` solely because two bytes were `MZ` |
| Executable-shaped prefix, no archive needle, probe correctly rejects | Extension guess or `FormatDetectionError` — not a fabricated member |
