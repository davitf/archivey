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

### Investigation result — Brotli (and peers) vs executable-shaped prefixes

**Run 2026-08-19, before writing any detection code. Two of the proposal's premises did
not survive it.**

**Premise 1 — "`TruncatedError` counts as a hit" is not the mechanism.** The A-34 stub
(`MZ` + `\x90` × 4094) does not truncate: it decodes **252 bytes cleanly**. Treating
truncation as a miss would not have changed the answer.

**Premise 2 — "low-entropy `MZ` stub" describes a synthetic file, not a real one.**

| Input | Brotli probe |
| --- | --- |
| `MZ` + `\x90` × 4094 (the A-34 stub) | accepted |
| `MZ` + `\x00` × 4094, `MZ` + `A` × 4094 | rejected |
| Structurally valid PE stub (DOS header, `e_lfanew` → `PE\0\0`), any filler | rejected |
| Real `rar a -sfx` ELF stub (249 KB) | rejected |
| 887 real ELF binaries under `/usr/bin` + `/usr/lib` | **0** accepted |

So the *silent* path needs a hand-rolled stub; the loud path
(`FormatDetectionError`) is what a real SFX actually hits today — verified: a real
`rar a -sfx` archive raised `FormatDetectionError` on `main`, with its RAR magic at
offset 248 952.

**Candidate lever 3 (stricter / larger Brotli probe) is dead.** Measured against 15 real
Brotli streams (qualities 1/5/11 over text, random bytes, an ELF binary, an empty
payload, a one-byte payload) and 1 500 random 4 KiB blobs:

| Probe variant | Random-data FP | Real-Brotli misses |
| --- | --- | --- |
| 256-byte prefix, any outcome (today) | 8.27% | 0/15 |
| 256-byte prefix, require ≥1 byte out | 8.27% | 3/15 |
| 256-byte prefix, require ≥512 bytes out | 1.60% | 10/15 |
| 1024-byte prefix, any outcome | 8.27% | 0/15 |
| 4096-byte prefix, any outcome | 8.20% | 0/15 |
| 4096-byte prefix, require ≥512 bytes out | 8.13% | 6/15 |

A bigger prefix buys nothing; demanding output trades FP for FN one-for-one. The
looseness is in the bitstream, not the parameters.

**Chosen rule (maintainer: "as recommended", 2026-08-19) — lever 1 + a narrow lever 2:**

1. **SFX-scan-first on a weak cue.** Bare `MZ` / `\x7fELF` is enough to *look* for an
   appended archive. On a hit the archive wins; on a miss the content probes run exactly
   as before. False-negative risk for Brotli: **zero** — a needle miss changes nothing.
2. **Content probes suppressed on a strong cue.** A DOS header whose `e_lfanew` really
   points at `PE\0\0`, or an ELF ident block with valid `EI_CLASS` / `EI_DATA` /
   `EI_VERSION`, with no archive needle in the window → no probe runs; detection falls
   through to the extension guess or `FormatDetectionError`. Measured false-negative
   risk: 0/887 real ELF binaries were probe hits to begin with, and a Brotli stream that
   coincidentally forms a valid PE or ELF header is a ~2⁻³² event.
3. **The Brotli probe itself is untouched**, which is what keeps `.br` detection
   unchanged and leaves the residual free to be fixed properly.

**Accepted residual, stated plainly:** a weak cue with no archive in the window still
reaches the probes, so `MZ` + `\x90` × 4094 *with no payload* still detects as
`BROTLI`. That is not an SFX defect — it is the general one, and it is much wider than
`MZ`: **8.27% of arbitrary binary data** passes this probe (7.3% end-to-end through
`detect_format`). Narrowing the cue to bare `MZ` would fix two bytes' worth of a 2³²-wide
problem while taking on the false-negative risk MD2 warned about. Registered instead as
`open-issues.md` P12 and `threat-model.md` O10, with
`dev-docs/investigations/brotli-content-probe-brief.md` as the deep dive the maintainer
asked for — including the question that would let the cue tighten later: *can a legal
Brotli stream begin with `MZ` at all?*

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

**Settled at implement time** (see the investigation result above): the cue is
two-tiered. A **weak** cue (bare `MZ` / `\x7fELF`) triggers the scan only; the probes
still run on a miss, so nothing a probe used to detect stops being detected. A **strong**
cue (`e_lfanew` — `PE\0\0`, or a valid ELF ident block) additionally suppresses the
probes on a miss. The scan is what fixes every row of the SFX matrix; the strong-cue gate
is what keeps a confirmed executable from becoming a fabricated `*.uncompressed` member,
at a measured false-negative cost of zero.

### 2. One shared `SFX_MAX` (2 MiB) for RAR, detection, and 7z
**Landed in `src/archivey/internal/sfx.py`** (maintainer, 2026-08-19), not in
`detection.py` as task 1.3 first put it: importing the detector from `rar_parser` would
be a new backend-to-detector edge, and the constant is not the detector’s to own.
Nothing outside `rar_parser` imported `rar_parser.SFX_MAX`, so no re-export was needed.
`tests/atheris_fuzz/crc_fixup.py` keeps its own copy on purpose — a fuzz oracle that
imports the code under test cannot disagree with it.

Promote today’s `rar_parser.SFX_MAX` to a single shared constant (module/name
flexible) consumed by the RAR parser SFX scan, `detect_format`’s forward window,
and the 7z forced-format SFX scan. Value remains 2 MiB unless a later measured
change moves it for everyone. Near-EOF search is optional if the forward window
already covers typical stubs.
**Rejected:** three relative bounds (“≥ RAR’s window”) that can drift — #253 MD3 = A.
**Rejected:** unbounded scan; **Rejected:** a tinier detection-only window that
misses stubs RAR already accepts.

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

**Settled (maintainer, 2026-08-19): the argument, used by every SFX backend, and defined
in terms of the view.** `open_read(source, start_offset=N)` MUST behave exactly as if it
had been handed a view of `source` starting at N — nothing before N belongs to the
archive — with one permitted exception: a backend may hand an *external tool* the
original path when the tool finds the payload itself (`unrar` on an SFX file). Asserted
directly: `tests/test_sfx.py` opens each SFX format both ways and compares the members
byte for byte.
The argument rather than a slice at the call site, because slicing a `Path` source would
cost RAR a whole-archive temp copy for every compressed member
(`RarReader._ensure_archive_path`) — the E-71 / P11 cost this change must not add.
Each backend then applies it as a view internally: 7z rebases `SharedSource` through
`_view`, ZIP wraps its handle in a `SlicingStream` before `zipfile` sees it, RAR starts
its parse at the origin. Formats that cannot carry a stub refuse a nonzero value
(`reject_start_offset`) rather than silently reading from byte 0.
**Note:** all three SFX backends can already find the payload unaided (RAR scans, ZIP
reads the EOCD from the tail, 7z scans after the sibling change), so the offset is not
what makes SFX open. It is what pins *which* payload — for a caller that knows the
offset independently.

**Corrected after review (Cursor F2, 2026-08-20).** The first version of this note said a
stub carrying its own `PK\x03\x04` or `Rar!\x1a\x07` "cannot move the answer". That is
only true when the offset comes from somewhere other than the scan. On the *auto-detect*
path it does not: `detect_format` derives `payload_offset` from the same earliest-match
scan, so a decoy in the stub becomes the offset and the backend opens there. Measured on
this branch — stub `MZ` + `\x90`×512 + `MAGIC_7Z` + filler, real 7z at 4096:

| | detect | auto-open |
| --- | --- | --- |
| decoy + real 7z | `SEVEN_Z`, `payload_offset=514` (the decoy) | `CorruptionError: 7z signature header CRC mismatch` |
| decoy + real ZIP | `ZIP`, `payload_offset=514` (the decoy) | succeeds — `zipfile` finds the EOCD from the tail of the slice |

What the offset *does* still guarantee is the explicit hand-off: `open_read(source,
start_offset=N)` opens at N and never re-scans, which is what
`test_start_offset_is_believed_rather_than_rescanned` pins.

**Accepted for this change, as a known scanner limitation.** The failure is loud, not
silent, so it is not the defect class this change exists to close, and it needs a
hostile or unlucky stub to reach. Fixing it means validate-and-continue — on a
header/CRC rejection after an SFX hit, resume the scan past the decoy — which turns
detection into a trial-open loop and belongs in its own change if wild stubs ever show
it. `test_a_decoy_needle_in_the_stub_wins_and_fails_loudly` pins today's behaviour so
the choice stays visible.

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

## Known gap — Mach-O stubs are not a cue

`format-detection` names `MZ` and ELF, and `executable_cue` implements exactly those. A
macOS self-extracting stub is **Mach-O** (`0xfeedfacf`, or `0xcafebabe` for a universal
binary), which matches no cue, so an archive appended to one still falls through to the
content probes — the defect this change closes for the other two shapes.

**"Falls through to the content probes" is too gentle: on a thin 64-bit stub they always
claim it.** The Brotli investigation (`dev-docs/investigations/brotli-content-probe-results.md`
§7.2) found that `cf fa ed fe` is *structurally* a valid Brotli uncompressed meta-block
header — WBITS 24, `ISLAST` 0, six nibbles, a non-zero top MLEN nibble from `0xFE`,
`ISUNCOMPRESSED` from its bit 7, and zero padding. That depends on the four magic bytes
alone, so **every** thin 64-bit little-endian Mach-O qualifies regardless of `cputype`
(200 real hits on a macOS CI runner). Measured against this branch at `34db1b0`:

| stub + appended 7z | `executable_cue` | `detect_format` | `open_archive` |
| --- | --- | --- | --- |
| PE | `STRONG` | `SEVEN_Z`, `payload_offset=8132` | real members |
| ELF | `STRONG` | `SEVEN_Z`, `payload_offset=8192` | real members |
| **Mach-O (thin arm64)** | **`NONE`** | **`BROTLI`** | **one fabricated `.uncompressed` member** |

So the silent-wrong-answer defect this change exists to close is **intact on macOS** after
it lands — not "might be claimed", but reliably claimed. Two details for whoever fixes it:

- **Brotli is not the only claimant.** With a realistic arm64 header the answer is
  `BROTLI`; with a zero-filled stub, LZMA Alone's probe bites first and it is
  `LZMA_ALONE`. Both fabricate a single `.uncompressed` member, so the user-visible defect
  is identical — the fall-through lands on whichever content probe accepts first.
- **Only a *thin* stub is silent.** A universal (`0xcafebabe`) stub is rejected by both
  probes and fails loudly with `FormatDetectionError`. That makes the fat case the *safe*
  one, which is the opposite of what the `0xcafebabe`/Java-class-file caveat below might
  lead a reader to assume.

The fix is proposed as `openspec/changes/prefixed-archive-detection`, which widens the cue
set — the spec change this section defers.

Surfaced by CI rather than reasoned about in advance: the first version of
`test_a_real_elf_binary_is_a_strong_cue` sampled `/usr/bin/env`, which is ELF on Linux
and a Mach-O universal binary on macOS, so both macOS legs went red on a real
platform difference. The test now skips where the platform's binaries are not ELF, and
`test_a_mach_o_binary_is_not_a_cue_today` pins the gap so it is recorded behaviour
rather than a surprise.

Widening the cue is a **spec change**, not an implementation detail, so it is
deliberately not done here. If it is wanted: the magics are the four Mach-O variants
plus the two fat ones, and a strong cue would validate `cputype` / `filetype` the way
the PE path validates `e_lfanew`. One caveat for whoever picks it up — the fat magic
`0xcafebabe` is also the Java class-file magic, so a bare-magic (weak) match would gate
content probes on `.class` files unless the strong check is required.

## Risks / Trade-offs

- [Real Brotli with executable-looking prefix] — **Closed by the two-tier cue.** A
  weak `MZ` / ELF prefix never suppresses a probe, so no stream a probe used to detect
  stops being detected; only a structurally confirmed PE/ELF does, at a measured 0/887
  on real binaries. Bare brotli streams with non-executable prefixes are untouched.
- [7z auto-open still broken until sibling] — **Closed:** the sibling landed first in
  the same PR, so 7z SFX auto-opens end to end.
- [Large SFX stubs beyond window] → Shared `SFX_MAX` (2 MiB); raising it is one
  edit for RAR, detection, and 7z.
- [RAR stream temp spill x `payload_offset` (E-71 / P11)] — **Decided: payload-only.**
  `_ensure_archive_path` now copies from the archive origin, so a stream source spills
  a plain RAR rather than stub+payload — smaller, and it does not depend on `unrar`
  re-finding the magic. A *path* source is unaffected: it keeps its own path and
  `unrar` reads the SFX directly. This changes no cost *class* — the spill P11
  describes still happens, still unsignalled — so P11 is untouched, and this change does
  not invent a second spill.

## Open Questions

- ~~**Differentiation policy:** which combination of stronger executable cues vs
  stricter Brotli probe vs scan-first-then-probe lands.~~ **Answered** by the
  investigation above and the maintainer’s "as recommended" (2026-08-19):
  scan-first on a weak cue, probes suppressed on a strong one, Brotli probe untouched.
- **Not blocking this change:** whether a legal Brotli stream can begin with `MZ` at
  all. If it cannot, the probe suppression can widen from strong cues to any `MZ`
  prefix. Deliberately left to
  `dev-docs/investigations/brotli-content-probe-brief.md` rather than guessed at here.
