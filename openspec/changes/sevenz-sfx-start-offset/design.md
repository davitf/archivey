## Context

`format-detection` already says native RAR/7z parsers SHALL accept a start offset
for SFX. RAR implements this (`_find_sfx_header`, up to 2 MiB). 7z’s
`read_signature_and_next_header` always `seek(0)` and requires `MAGIC_7Z` at the
file origin, so forced `format=SEVEN_Z` on an SFX stub raises `CorruptionError`.
`format-7z` never states the start-offset obligation. Found via Topic 8 #252 A-34.

## Goals / Non-Goals

**Goals:**
- 7z open succeeds when the signature header starts at a non-zero offset (forced
  `format=SEVEN_Z` on SFX, and detection-supplied `payload_offset`).
- Read in place — no whole-archive temp copy.
- Spec `format-7z` so the obligation is local to the format, not only buried in
  `format-detection`.

**Non-Goals:**
- Implementing `detect_format` SFX scan (sibling `sfx-format-detection`).
- Changing solid/folder decode, codecs, or multi-volume joining.
- ZIP or other formats’ SFX.

## Investigations

| Observation | Evidence |
| --- | --- |
| RAR SFX on forced format | `rar_parser._find_sfx_header`; #252 repro |
| 7z magic-at-zero only | `sevenzip_parser.py` `fp.seek(0)` then `MAGIC_7Z` check |
| Packed-stream offsets are relative to signature | Signature header at 32 + pack_pos; a non-zero archive origin must shift all absolute seeks |
| Detection hand-off | Sibling change wires `payload_offset`; this change must accept that offset |

## Decisions

### 1. Accept an explicit start offset on the 7z open/parse path
Thread a `start_offset: int = 0` (name flexible) from reader open into
`read_signature_and_next_header` (and any absolute seeks derived from the
signature). Magic is checked at `start_offset`, not necessarily file byte 0.
**Rejected:** copying the suffix to a temp file (conflicts with ADR 0010 / cost
honesty).

### 2. Also scan when magic is missing at the open position (forced-format parity)
When `format=SEVEN_Z` is forced and magic is not at the current origin, scan
forward within a bound aligned with RAR’s `SFX_MAX` (2 MiB) for `MAGIC_7Z`, then
continue from that offset. **Rejected:** forced-format-only failure until the
caller passes an offset (breaks today’s RAR-shaped escape hatch for 7z).

### 3. Keep absolute geometry relative to the signature origin
Once the signature is found at offset S, treat subsequent header/pack seeks as
`S + relative` (today’s code assumes S=0). Document the invariant in the parser
module.

### 4. Spec lives primarily in `format-7z`
Add an explicit requirement so format owners see it next to other 7z contracts.
`format-detection`’s cross-cutting sentence remains; this change does not remove it.

## Risks / Trade-offs

- [Missed seek sites after introducing S] → Audit all `seek`/`tell` in 7z parser
  / pipeline against the signature origin; add an SFX fixture that exercises
  packed streams, not only empty archives.
- [Scan cost on every forced open] → Fast path: magic at origin unchanged; scan
  only on miss.

## Open Questions

None blocking the proposal.
