## Context

`openspec/specs/format-detection/spec.md` already requires SFX detection behind
`MZ` / ELF stubs with `payload_offset`, and says native RAR/7z parsers SHALL
accept a start offset. Shipped `detect_format` has no SFX scan (`detection.py`
defers it). `FormatInfo.payload_offset` exists but `open_archive` only consumes
`.format` / `.encoding_hint`. Content probes (Brotli) can claim an executable
stub before any archive magic deeper in the file is considered.

Topic 8 #252 / A-34 reproduced: low-entropy `MZ` + RAR/7z/ZIP payload → detect as
`BROTLI`, auto-open succeeds with fabricated `*.uncompressed`. Varied stub →
`FormatDetectionError`. Forced `format=RAR` works via parser SFX; forced
`SEVEN_Z` fails until `sevenz-sfx-start-offset`; forced `format=ZIP` already
succeeds (zipfile finds EOCD from the tail).

Deferred historically in Phase 3 (`SFX → Phase 7`); native 7z/RAR later landed
without finishing detection-side SFX. Not a `dev-docs/open-issues.md` P-entry.

**Maintainer (PR #253 F1 = A, 2026-08-19):** include ZIP local-header
(`PK\x03\x04`) in the SFX scan — same silent-`BROTLI` defect, most common wild
SFX form; incremental cost is a needle plus a matrix row.

**Maintainer (PR #253 MD2 = A + investigation, 2026-08-19):** split “no silent
wrong answer on executable-shaped prefix” into its own requirement. Do **not**
hard-wire “disable Brotli on `MZ`” — a real Brotli stream can begin with
executable-looking bytes. Investigate differentiation before locking the probe
policy.

## Goals / Non-Goals

**Goals:**
- Implement SFX scan in `detect_format` for RAR, 7z, and ZIP local-header magic
  within a bounded window.
- Prevent content probes from producing a *silent wrong answer* on
  executable-shaped stubs, without falsely rejecting real probe-matched streams
  whose prefix happens to look executable.
- Hand `payload_offset` to the open path so backends read in place from the payload.
- Tests that fail on the silent-success path (not only the raising path).

**Non-Goals:**
- Implementing 7z parser start-offset / SFX scan (sibling `sevenz-sfx-start-offset`).
- Other formats’ embedded payloads beyond RAR / 7z / ZIP (e.g. TAR-behind-stub).
- Changing ZIP’s EOCD-from-tail open behaviour (already correct under forced
  `format=ZIP`); this change only teaches *detection* to find ZIP SFX.
- Changing the public `FormatInfo` shape.
- Guide prose (Topic 8 page PRs after this lands).
- Prematurely locking a Brotli-vs-SFX heuristic before the investigation below.

## Investigations

| Observation | Evidence |
| --- | --- |
| Spec already normative | `format-detection` §SFX; `payload_offset` on `FormatInfo` |
| Detection deferred in code | `detection.py` module docstring |
| `payload_offset` unused at open | `core.py` uses `detected.format` / `encoding_hint` only |
| Silent path (RAR) | `MZ`+`0x90`×4094 + RAR → `BROTLI` / open OK / bogus member |
| Silent path (ZIP) | Same stub + ZIP → `BROTLI` / fabricated `*.uncompressed`; forced ZIP → real members (#253 F1) |
| Loud path | Varied stub → `FormatDetectionError` |
| RAR forced-format OK | `rar_parser._find_sfx_header`, `SFX_MAX = 2 MiB` |
| 7z forced-format fails | `sevenzip_parser.read_signature_and_next_header` seeks 0, magic at byte 0 |
| ZIP forced-format OK | `zipfile` EOCD from tail; stub ignored |
| Brotli probe is weak | `BrotliCodec.content_probe` → `_decodes_sample`; `_PROBE_PREFIX = 256`; `TruncatedError` counts as a hit — low-entropy stubs can “decode” briefly then fabricate a member |

### Open investigation — Brotli (and peers) vs executable-shaped prefixes

**Problem.** Today’s failure is “stub looks like Brotli for 256 bytes.” Blindly
skipping content probes whenever the prefix is `MZ` / ELF would fix SFX but can
miss a genuine Brotli (or zlib) stream whose first bytes coincide with a weak
executable cue. `MZ` alone is only two bytes; real PE/ELF/SFX stubs carry more
structure.

**Candidate levers** (evaluate in the implement PR; pick one or a combination;
record measurements):

1. **SFX-scan-first, then probe on miss** — always search RAR/7z/ZIP needles in
   the window when a *strong* executable cue matches; only then run content
   probes. Preserves real Brotli-with-`MZ`-prefix after a needle miss.
2. **Stronger executable cues** — require PE (`e_lfanew` → `PE\0\0`), ELF class /
   data / version fields, or known SFX stub fingerprints, not bare `MZ`.
3. **Stricter / larger Brotli probe** — raise `_PROBE_PREFIX`, require non-empty
   output, and/or stop treating bare `TruncatedError` as success when the prefix
   looks executable-shaped; measure false-negative rate on real `.br` fixtures.
4. **Hybrid** — weak cue → scan-first; strong PE/ELF → scan-first + stricter
   probe gate on miss.

**Acceptance for the investigation:** silent SFX→Brotli path stays red–green
covered; at least one constructed “Brotli whose prefix looks weakly executable”
case still detects as Brotli (or documents why that case is vanishingly rare and
accepted).

## Decisions

### 1. SFX scan runs before content probes when the prefix looks executable-shaped
If the peeked prefix matches the (investigation-refined) executable cues, run the
SFX magic scan first (RAR / 7z / ZIP local-header needles). Probe policy on miss
is **not** “always disable Brotli” — it follows the differentiation investigation
above. **Rejected:** scan after probes (preserves today’s silent BROTLI bug).
**Rejected:** RAR/7z-only scan (leaves ZIP SFX at `FormatDetectionError` for a
file forced `format=ZIP` already reads — #253 F1 = A).
**Rejected (MD2):** nesting the no-silent-wrong-answer rule only inside the SFX
requirement (easy to narrow away later; split into its own requirement).
**Rejected without measurement:** hard-disable content probes on bare `MZ`
(risks missing real Brotli — maintainer note on MD2).

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
At least one test builds a low-entropy `MZ` stub + real RAR, one with ZIP, and
(once the sibling lands) 7z, and asserts: not `BROTLI`, and auto-open does not
return a fabricated single-file member. **Rejected:** only testing
`FormatDetectionError` on varied stubs.

### 5. Pair with `sevenz-sfx-start-offset`
Detection can return `SEVEN_Z` + offset before 7z accepts offsets; auto-open of 7z
SFX then fails until the sibling lands. Land 7z start-offset first or in the same
PR train. RAR auto-open works once detection sets offset *or* once the opener seeks
and RAR’s own scanner still finds magic at the new origin.

## Risks / Trade-offs

- [Real Brotli with executable-looking prefix] → Do not hard-disable probes on
  bare `MZ`; complete the differentiation investigation (stronger cues / stricter
  probe / scan-first-then-probe). Bare brotli streams with non-executable prefixes
  stay unchanged.
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

- **Differentiation policy (blocking for implement, not for this proposal):** which
  combination of stronger executable cues vs stricter Brotli probe vs
  scan-first-then-probe lands — see Investigations. Window size still defaults to
  RAR’s 2 MiB unless implementation finds a reason to share one constant.
