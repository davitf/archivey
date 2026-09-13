## ADDED Requirements

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
