## Why

Forced `format=RAR` opens an SFX file because the RAR parser scans up to 2 MiB for
magic (`_find_sfx_header`). Forced `format=SEVEN_Z` on the same shape raises
`CorruptionError: Not a 7z archive: bad magic bytes` — `read_signature_and_next_header`
always `seek(0)` and checks magic at byte 0. `format-detection` already says native
RAR/7z parsers SHALL accept a start offset; `format-7z` never states it, and the 7z
reader never implements it. Topic 8 (#252) found this asymmetry while verifying A-34;
it is not a separate `open-issues.md` P-entry.

## What Changes

- Teach the 7z parser/reader to accept a start offset (and/or scan for `7z` magic
  within a bounded SFX window when magic is not at the open position), matching RAR’s
  “forced format still works on SFX” behaviour.
- Read the archive in place from that offset — no whole-file copy.
- Align `format-7z` with the start-offset obligation already in `format-detection`.
- No **BREAKING** API: forced `format=SEVEN_Z` on SFX becomes success instead of
  `CorruptionError`.

## Capabilities

### New Capabilities

### Modified Capabilities
- `format-7z` — require accepting a non-zero archive start offset / SFX stub in front
  of the signature header (parity with RAR’s parser-side SFX).

## Impact

- Modules: `sevenzip_parser.py` (`read_signature_and_next_header` and callers),
  `sevenzip_pipeline.py` / `sevenzip_reader.py` open path.
- Public API: `open_archive(..., format=SEVEN_Z)` and auto-open once detection sets
  `payload_offset` (sibling change `sfx-format-detection`).
- Tests: SFX 7z fixtures with stub prefixes; forced-format and offset hand-off cases.
- Pairs with: `sfx-format-detection` (detection + `payload_offset` wiring). This change
  can land first — forced `format=SEVEN_Z` becomes usable on SFX even before detection
  scans — and is required for auto-open of 7z SFX after detection lands.
