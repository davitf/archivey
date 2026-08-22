## Why

An archive does not have to start at byte zero, and archivey currently finds only one
shape of that: an executable stub in front of RAR or 7z. The shipped `format-detection`
requirement says so literally — *"If leading bytes look like executable (`MZ` / ELF)"*.

That misses formats that are neither rare nor exotic. Measured on `main`:

| file | `detect_format` | `open_archive(format=ZIP)` | stdlib `zipfile` |
| --- | --- | --- | --- |
| `zipapp` output (`#!/usr/bin/env python3` + ZIP) | `FormatDetectionError` | **opens, all members** | opens |
| Spring Boot executable JAR (`#!/bin/sh` + ZIP) | `FormatDetectionError` | **opens** | opens |
| JPEG with an appended ZIP | `FormatDetectionError` | **opens** | opens |
| makeself `.run` (`#!/bin/sh` + tar.gz) | `FormatDetectionError` | fails — needs an offset | n/a |

The ZIP rows are the embarrassing ones: **the backend already reads these files**, and
`zipapp` is in the Python standard library. Only detection refuses, and archivey is
currently worse than `zipfile` on a `.pyz`. The cause is that the executable cue never
fires on a `#!` prefix.

The gap is not "we forgot to scan harder". Detection is deliberately cheap, and the
existing cue exists to avoid reading up to `SFX_MAX` (2 MiB) from every file someone opens.
The fix is to notice that **different formats make that cost wildly different**, and to
tier detection accordingly instead of applying one rule to all of them.

## What Changes

- **Always probe the tail for self-locating containers, when the source is seekable.**
  ZIP records its central directory from the end and its offsets are relative to the ZIP's
  own start, so a prefixed ZIP needs no scan and no offset — only the willingness to look.
  The search is bounded *by the format*: the EOCD comment length is a `uint16`, so
  65535 + 22 bytes is a hard ceiling, not a tuning choice.
- **Keep the forward scan gated, but widen the gate** from "leading bytes look executable"
  to "leading bytes look like a *prefix*": `MZ`, ELF, **Mach-O**, or a `#!` shebang. Same
  window, same cost, and it covers the `.run` installer family (makeself, NVIDIA, Anaconda)
  for every payload format — the one case the ZIP tail probe cannot reach.

  **The Mach-O half is a live defect, not a tidy-up.** `sfx-format-detection` names `MZ`
  and ELF, so a macOS SFX stub matches no cue — and `cf fa ed fe` is *structurally
  guaranteed* to parse as a Brotli uncompressed meta-block header. Measured against that
  change's own HEAD (`34db1b0`): a PE stub and an ELF stub both open the real 7z members,
  while a Mach-O stub returns `BROTLI` with one fabricated `.uncompressed` member. The
  silent-wrong-answer defect #254 exists to close is therefore intact on macOS after it
  lands. That change records the gap and deliberately defers the fix, because widening the
  cue set is a spec change; **this is that spec change**.
- **Validate a scan hit instead of trusting the magic.** A 7z signature self-checks from
  its own 32 bytes (`StartHeaderCRC` over the 20-byte StartHeader, plus
  `offset + 32 + NextHeaderOffset + NextHeaderSize` landing at EOF); RAR5's 8-byte marker
  is followed by a CRC-checked main header. This does not change *when* we scan — cost
  still decides that — but it means a hit is essentially never wrong, so it can be reported
  at high confidence.
- **Add an opt-in exhaustive scan**, off by default, for callers who know they are holding
  a firmware image or a disk image.
- **Report what is in front of the payload**, so a caller can tell a self-extracting archive
  from an archive that merely happens to be embedded in something else.
- **Register the ZIP-family extensions that are missing** (`.jar`, `.pyz`, `.whl`, `.apk`),
  since today not even the extension fallback rescues these files.

## Capabilities

### New Capabilities

### Modified Capabilities

- `format-detection` — the SFX requirement is replaced by a tiered "the archive may start
  after byte zero" rule: an always-on tail probe for self-locating containers, a
  prefix-cued forward scan for the rest, an opt-in exhaustive scan, and a `prefix_kind`
  on `FormatInfo`.
- `format-zip` — a leading prefix is explicitly supported rather than incidental, and the
  EOCD search bound is stated as a format-derived constant.
- `archive-reading` — `open_archive` gains the exhaustive-scan opt-in and passes it through.

## Decisions

- **Cost tiers the design, not exhaustiveness.** The cue was never about false positives;
  it was about not reading 2 MiB from every file. Once that is the stated reason, the
  answer follows: a probe the *format* bounds (ZIP's 64 KiB tail) can run always, a scan
  bounded only by a constant we picked needs a reason to run, and an unbounded sweep is
  opt-in. Recorded rather than assumed, because the previous cue looked like a
  false-positive defence and was reasoned about that way in review.
- **Opening an embedded archive is the right default.** A caller who opens a file has a
  reason to think it is an archive; if they are sweeping everything, the `prefix_kind`
  field lets them filter. archivey reports what it found rather than guessing intent.
- **Scan validation is not a licence to scan more.** The 7z/RAR self-checks make a hit
  trustworthy, not cheap. They justify high confidence on a hit, not removing the gate.

## Impact

- Modules: `src/archivey/internal/detection.py` (tier order, tail probe, widened cue,
  `prefix_kind`), `src/archivey/internal/sfx.py` (cue + validated scan), the ZIP backend's
  prefix handling, `open_archive`'s new argument.
- Public API: `FormatInfo` gains `prefix_kind`; `open_archive` / `detect_format` gain the
  exhaustive-scan opt-in. Files that used to raise `FormatDetectionError` now open — a
  behaviour change, and the point of the change.
- Tests: `zipapp`, Spring Boot exec JAR, polyglot, makeself, 7z/RAR SFX with PE, ELF,
  Mach-O and shebang stubs; non-seekable sources; the opt-in scan.
- Docs: `docs/formats.md` detection prose.
- **Supersedes** the SFX requirement `sfx-format-detection` (#254) established, and modifies
  the *Executable-looking prefixes* requirement it added alongside — both now live, since
  #258 archived that change. The prerequisite is satisfied; see `design.md` §Sequencing.
