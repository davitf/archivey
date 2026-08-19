## Context

`openspec/specs/format-detection/spec.md` already requires SFX detection behind
`MZ` / ELF stubs with `payload_offset`, and says native RAR/7z parsers SHALL
accept a start offset. Shipped `detect_format` has no SFX scan (`detection.py`
defers it). `FormatInfo.payload_offset` exists but `open_archive` only consumes
`.format` / `.encoding_hint`. Content probes (Brotli) can claim an executable
stub before any archive magic deeper in the file is considered.

Topic 8 #252 / A-34 reproduced: low-entropy `MZ` + RAR/7z payload → detect as
`BROTLI`, auto-open succeeds with fabricated `*.uncompressed`. Varied stub →
`FormatDetectionError`. Forced `format=RAR` works via parser SFX; forced
`SEVEN_Z` fails until `sevenz-sfx-start-offset`.

Deferred historically in Phase 3 (`SFX → Phase 7`); native 7z/RAR later landed
without finishing detection-side SFX. Not a `dev-docs/open-issues.md` P-entry.

## Goals / Non-Goals

**Goals:**
- Implement SFX scan in `detect_format` for RAR and 7z magic within a bounded window.
- Prevent content probes from silently winning on executable stubs.
- Hand `payload_offset` to the open path so backends read in place from the payload.
- Tests that fail on the silent-success path (not only the raising path).

**Non-Goals:**
- Implementing 7z parser start-offset / SFX scan (sibling `sevenz-sfx-start-offset`).
- ZIP SFX / other formats’ embedded payloads.
- Changing the public `FormatInfo` shape.
- Guide prose (Topic 8 page PRs after this lands).

## Investigations

| Observation | Evidence |
| --- | --- |
| Spec already normative | `format-detection` §SFX; `payload_offset` on `FormatInfo` |
| Detection deferred in code | `detection.py` module docstring |
| `payload_offset` unused at open | `core.py` uses `detected.format` / `encoding_hint` only |
| Silent path | `MZ`+`0x90`×4094 + RAR → `BROTLI` / open OK / bogus member |
| Loud path | Varied stub → `FormatDetectionError` |
| RAR forced-format OK | `rar_parser._find_sfx_header`, `SFX_MAX = 2 MiB` |
| 7z forced-format fails | `sevenzip_parser.read_signature_and_next_header` seeks 0, magic at byte 0 |

## Decisions

### 1. SFX scan runs before content probes when the prefix looks executable
If the peeked prefix matches executable cues (`MZ` / ELF), run the SFX magic scan
first. Only if no RAR/7z magic is found may content probes / extension fallback
run. **Rejected:** scan after probes (preserves today’s silent BROTLI bug).

### 2. Bounded forward window aligned with RAR’s existing SFX_MAX (2 MiB)
Reuse the same order-of-magnitude bound as `rar_parser.SFX_MAX` for detection so
forced-format RAR and auto-detect agree on what is reachable. Near-EOF search is
optional if the forward window already covers typical stubs; document the chosen
bound in the delta. **Rejected:** unbounded scan; **Rejected:** tiny window that
misses real SFX stubs RAR already accepts.

### 3. Open path honours `payload_offset` via start-offset arg or offset view
After detection returns `payload_offset > 0`, the opener SHALL either pass an
explicit start-offset into `backend.open_read` or wrap the source in a bounded
offset view / slice whose byte 0 is the payload. A bare seek on a shared handle
is insufficient — 7z’s parser absolute-seeks to 0 and then to
`_SIGNATURE_HEADER_SIZE + next_header_offset`, discarding caller positioning
(see sibling `sevenz-sfx-start-offset` Decision 1). Prefer read-in-place over
copying the remainder.
**Rejected:** re-implementing SFX only inside each backend for auto-detect (leaves
`payload_offset` dead and duplicates RAR’s scanner).
**Rejected:** bare seek alone as the hand-off (works for RAR’s forward scan, fails
for 7z).

### 4. Silent-success regression is mandatory
At least one test builds a low-entropy `MZ` stub + real RAR (and 7z once sibling
lands) and asserts: not `BROTLI`, and auto-open does not return a fabricated
single-file member. **Rejected:** only testing `FormatDetectionError` on varied stubs.

### 5. Pair with `sevenz-sfx-start-offset`
Detection can return `SEVEN_Z` + offset before 7z accepts offsets; auto-open of 7z
SFX then fails until the sibling lands. Land 7z start-offset first or in the same
PR train. RAR auto-open works once detection sets offset *or* once the opener seeks
and RAR’s own scanner still finds magic at the new origin.

## Risks / Trade-offs

- [Brotli-of-EXE false negative] → Only suppress/reorder probes when the prefix is
  executable-shaped; bare brotli streams unchanged.
- [7z auto-open still broken until sibling] → Tasks call out dependency; forced
  `format=SEVEN_Z` fixed by sibling alone.
- [Large SFX stubs beyond window] → Same limit as RAR parser today; document.
- [RAR stream temp spill × `payload_offset` (E-71 / P11)] → Seekable-stream RAR
  already copies to a temp path for `unrar` (`RarReader._ensure_archive_path`).
  Once detection supplies a non-zero `payload_offset`, decide in the implement
  PR whether the temp holds payload-only (offset consumed) or stub+payload
  (`unrar` re-finds magic). Keep that choice consistent with P11’s eventual cost
  signal; this change does not invent a second spill.

## Open Questions

None for proposal scope — window size defaults to RAR’s 2 MiB unless implementation
finds a reason to share one constant.
