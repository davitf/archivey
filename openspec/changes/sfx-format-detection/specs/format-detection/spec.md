## MODIFIED Requirements

### Requirement: Self-extracting (SFX) archives are detected behind an executable stub

SFX RAR/7z/ZIP: EXE stub precedes payload. If leading bytes look like executable
(`MZ` / ELF) rather than archive magic, the system SHALL scan for RAR
(`52 61 72 21 1A 07`), 7z (`37 7A BC AF 27 1C`), or ZIP local-header
(`50 4B 03 04`) magic within a bounded forward window (at least as large as the
RAR parser’s SFX window) before running content probes. Match → embedded format
with `payload_offset` = payload start and `detected_by` indicating an SFX scan.
No match → fall through (extension / `FormatDetectionError`).

When the prefix looks executable, a content probe SHALL NOT claim a stream codec
(e.g. Brotli) and allow a successful open of a fabricated member — that is a
silent wrong answer. Native RAR/7z parsers SHALL accept a start offset (read in
place, no copy). ZIP already locates the EOCD from the tail, so a leading stub
is tolerated without a separate parser scan; detection still SHALL return `ZIP`
with `payload_offset` rather than a stream codec.

#### Scenario: SFX matrix

| Case | Expected |
| --- | --- |
| `MZ` + 7z magic at offset N | `SEVEN_Z`, `payload_offset == N`; backend opens at N |
| `MZ` + RAR magic at offset N | `RAR`, `payload_offset == N`; backend opens at N |
| `MZ` + ZIP local magic (`PK\x03\x04`) at offset N | `ZIP`, `payload_offset == N`; real ZIP members listed |
| `MZ` + low-entropy filler + RAR/7z/ZIP magic in window | Same as above — **not** `BROTLI` / fabricated single-file member |
| Executable header, no RAR/7z/ZIP in window | No SFX match; extension or `FormatDetectionError` |
| Bare brotli / non-executable stream | Unchanged content-probe behaviour |
