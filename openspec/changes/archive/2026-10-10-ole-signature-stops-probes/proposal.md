# Stop the content probes on an OLE compound file

## Why

An OLE compound file (`.doc`, `.xls`, `.ppt`, `.msi`, `Thumbs.db`) is a constant header
followed by long zero runs. With no archive extension, the Brotli probe claimed a 256 KiB
OLE header over zeros as `BROTLI` / `GUESS`, and the LZMA Alone probe claimed a 4 KiB one
as `LZMA_ALONE` / `PROBABLE`. A scan of a backup drive found 437 files claimed as Brotli,
all but two of which failed to decode (`dev-docs/investigations/2026-10-backup-scan.md`
§3.2; the OLE files among them were not counted separately).

## What changes

The OLE signature `D0 CF 11 E0 A1 B1 1A E1` at offset 0 stops the content probes and the
SFX scan. Detection falls through to the extension guess or `FormatDetectionError`, the
same as after a strong executable cue. The error names the evidence that stopped the
probes, for the OLE signature and for a strong executable cue.

Alternatives not taken:

- **A threshold on the probes.** `format-detection` forbids it: each one measured traded
  false positives for false negatives about one for one.
- **A check from Brotli's own framing.** None rejects this input. The OLE header parses as
  an uncompressed meta-block whose declared length fits any file of 7 425 bytes or more,
  and the chain walk finds a fitting chain. The Alone probe has the same problem: a range
  coder decodes zero bytes without error.
- **Treat OLE as an executable cue.** A cue starts the SFX scan, which reads up to 2 MiB
  of every `.doc`, and a ZIP stored inside a document would then be reported as the
  file's payload.

## Impact

- `format-detection`: one added requirement; the content-probe requirement names the
  exception; the OLE rows of the framing-gate and chain-walk matrices, and a stale COFF
  row, are corrected.
- `error-handling`: the limit-trip example no longer says an OLE file reaches the Alone
  probe.
- Code: `src/archivey/internal/detection.py` (steps 2 and 5, and the final error).
