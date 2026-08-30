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
- Implementing `detect_format` SFX scan (sibling `sfx-format-detection`, which
  covers RAR / 7z / ZIP needles).
- Changing solid/folder decode, codecs, or multi-volume joining.
- ZIP parser changes (ZIP SFX already opens under forced `format=ZIP` via EOCD
  from the tail; detection-side ZIP needle is the sibling’s job).

## Investigations

| Observation | Evidence |
| --- | --- |
| RAR SFX on forced format | `rar_parser._find_sfx_header`; #252 repro |
| 7z magic-at-zero only | `sevenzip_parser.py` `:357` `fp.seek(0)` then `MAGIC_7Z`; `:388` absolute next-header seek; pipeline has none |
| Packed-stream offsets are relative to signature | Signature header at 32 + pack_pos; a non-zero archive origin must shift all absolute seeks |
| Detection hand-off | Sibling change wires `payload_offset`; this change must accept that offset |

## Decisions

### 1. Accept an explicit start offset on the 7z open/parse path
Thread a `start_offset: int = 0` from `ReadBackend.open_read` into `SevenZipReader`.
Magic is checked at `start_offset`, not necessarily file byte 0.
**Rejected:** copying the suffix to a temp file (conflicts with ADR 0010 / cost
honesty).

**Amended at implement time (2026-08-19): the offset is applied as a bounded view,
not threaded into the parser.** The proposal assumed `read_signature_and_next_header`
absolute-seeks the *file*. It does not: the reader hands it `self._shared.view(0)`, a
`SlicingStream`, so its `seek(0)` and its
`seek(_SIGNATURE_HEADER_SIZE + next_header_offset)` are already view-relative — as are
`encoded_folder_slices`' "absolute" offsets and the pack-stream views. Every 7z offset
is signature-relative by construction, so the whole geometry rebases by giving the
reader one origin (`SevenZipReader._view`, two call sites) instead of teaching each
seek about stubs. Verified before implementing: an unmodified `main` opens a stubbed
7z with an encrypted encoded header and packed streams when handed
`SlicingStream(fp, start=stub_len)`, listing and reading every member.
The parser therefore keeps a single contract — *`fp` begins at the signature header* —
which is also what keeps `parse_sevenzip_archive` and the fuzz harnesses honest.
**Rejected (was Decision 1 as proposed):** `read_signature_and_next_header(fp,
start_offset=…)` plus per-seek arithmetic — two mechanisms for one origin (the reader
still needs its own for pack views), and a parser that can silently read a stub.

### 2. Also scan when magic is missing at the open position (forced-format parity)
When `format=SEVEN_Z` is forced and magic is not at the current origin, scan
forward within the **shared** `SFX_MAX` (same constant as RAR’s parser and
`detect_format`; today 2 MiB) for `MAGIC_7Z`, then continue from that offset.
**Rejected:** forced-format-only failure until the caller passes an offset (breaks
today’s RAR-shaped escape hatch for 7z).
**Rejected:** a separate 7z-only bound described as “≥ RAR’s window” (#253 MD3 = A —
one constant, three call sites).

### 3. Keep absolute geometry relative to the signature origin
Once the signature is found at offset S, subsequent header/pack seeks are
`S + relative` (today’s code assumes S=0). Per the Decision 1 amendment this is
structural rather than arithmetic: `SevenZipReader._view(start, length)` adds S, and
nothing downstream of it knows S exists. The invariant is documented on `_view` and on
`read_signature_and_next_header`.

**Semantics of `start_offset`, settled with the maintainer (2026-08-19):** a backend
handed `start_offset=N` MUST behave exactly as if it had been handed a view of the
source starting at N — nothing before N belongs to the archive. `open_read(path,
start_offset=N)` and `open_read(SlicingStream(fp, start=N))` are therefore required to
be observationally identical, which is asserted directly in `tests/test_sfx.py`. The
one permitted difference is a backend that hands an *external tool* the original path
because the tool finds the payload itself (`unrar` on an SFX file).

### 4. Spec lives primarily in `format-7z`
Add an explicit requirement so format owners see it next to other 7z contracts.
`format-detection`’s cross-cutting sentence remains; this change does not remove it.

## Risks / Trade-offs

- [Missed seek sites after introducing S] → **Resolved by the Decision 1 amendment.**
  The two absolute seeks in `sevenzip_parser.py` (`read_signature_and_next_header`
  line ~357 `fp.seek(0)` and line ~388 `fp.seek(_SIGNATURE_HEADER_SIZE +
  next_header_offset)`) run against a view, not the file, so neither needed rebasing —
  and the suspicion behind the note was right: pack offsets *are* applied by slicing
  (`SharedSource.view`, and `SlicingStream` in `decode_encoded_header`) rather than by
  a literal `.seek(`, which is exactly why rebasing the view catches them all and
  rebasing seeks would not have. The pack-stream + encoded-header SFX fixture is kept
  regardless: it is what proves the claim.
- [Scan cost on every forced open] → Fast path: magic at origin unchanged; scan
  only on miss.

## Open Questions

None blocking the proposal.
