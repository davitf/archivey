## Why

`format-detection` already requires SFX RAR/7z detection behind an executable stub
(`payload_offset > 0`), but `detect_format` never scans — the module comment defers it.
Topic 8 claim A-34 measured the worst consequence: a low-entropy `MZ` stub can be claimed
by the Brotli content probe, and `open_archive` then succeeds with a fabricated
`*.uncompressed` member (silent wrong answer). `payload_offset` is never consumed by
`open_archive` even when detection would set it. Found by the docs-content verification
pass (#252); not yet a `dev-docs/open-issues.md` P-entry.

## What Changes

- Implement the existing SFX scan in `detect_format` (bounded window for RAR/7z magic
  when leading bytes look like `MZ` / ELF).
- Order the scan **before** content probes so an executable stub cannot be silently
  claimed as Brotli (or similar).
- Wire `FormatInfo.payload_offset` through `open_archive` so backends open at the
  payload start (read in place, no copy).
- Red–green tests that cover the **silent-success** path specifically (not only
  `FormatDetectionError`).
- No **BREAKING** public API change: `FormatInfo.payload_offset` already exists; it
  becomes non-zero for real SFX inputs.

## Capabilities

### New Capabilities

### Modified Capabilities
- `format-detection` — tighten SFX vs content-probe ordering and the “no silent wrong
  format” obligation for executable stubs; keep the existing SFX matrix and
  `payload_offset` contract.
- `archive-reading` — open path SHALL honour a non-zero `payload_offset` from detection
  (or an equivalent start-offset hand-off to the backend).

## Impact

- Modules: `src/archivey/internal/detection.py`, `src/archivey/core.py` open path,
  backend `open_read` start-offset plumbing (RAR already scans internally; 7z needs
  sibling change `sevenz-sfx-start-offset`).
- Public API: behaviour of `detect_format` / auto `open_archive` on SFX inputs; CLI
  `info` already prints `sfx_offset` when set.
- Tests: SFX fixtures (RAR + 7z) with low-entropy and varied stubs; assert no silent
  `BROTLI` success; assert `payload_offset` and successful open of real members.
- Docs: unlocks writing `formats.md` Detection / SFX prose (Topic 8 A-34 / Q1).
- Depends on / pairs with: `sevenz-sfx-start-offset` for 7z forced-format and
  offset-accept parity (RAR already has parser-side SFX).
