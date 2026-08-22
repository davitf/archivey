## 0. Prerequisite

- [x] 0.1 ~~Land `sfx-format-detection` (#254) first~~ — merged as `6e71eba`. Its `payload_offset` hand-off is on `main`
- [x] 0.2 ~~**Archive `sfx-format-detection` before archiving this change.**~~ — done in #258 (`da427a0`). Both changes MODIFY the same SFX requirement, and this one's delta was written against #254's version rather than the then-shipped text, so the ordering mattered. Verified after the archive: the live requirement's three additions all survive in this delta — the ZIP local-header needle (tier 3), the backend-declared-needles rule (verbatim), and the weak/strong cue grading (in the sibling requirement below). Also restored the live spec's "a prefixed ZIP is reported as `ZIP` with a `payload_offset`, never as a stream codec" guarantee, which the tiering had left implicit
- [ ] 0.3 **This change now also MODIFIES `Executable-looking prefixes must not silently become a wrong stream format`**, added to the live spec by the same archive. It has to: that requirement enumerates the cue as `MZ` / `\x7fELF` only, which this change widens to Mach-O and `#!`, and it is where the macOS silent-wrong-answer defect is registered. Two notes for whoever archives:
  - `brotli-probe-framing-gate` (PR #255) does **not** modify this requirement — it cross-references it from its own added framing requirement instead — so the two changes' deltas are disjoint and may archive in either order
  - the live scenario row for a real Brotli stream said "still `BROTLI` / `PROBABLE`"; this delta drops the `PROBABLE`, because #255 moves an uncorroborated Brotli probe hit to `GUESS`. The row is about *which format* is reported, not its confidence, so dropping the confidence keeps it true under both changes

## 1. Tail probe for self-locating containers

- [ ] 1.1 Add a ZIP tail probe to `detect_format`, running after magic-at-0 and before the forward scan, only when the source is seekable
- [ ] 1.2 Bound the EOCD search at 65535 + 22 bytes and derive the constant in code from the `uint16` comment-length field, with a comment saying why it is not tunable
- [ ] 1.3 Report `payload_offset` at the start of the ZIP data, not 0, when a prefix is present
- [ ] 1.4 Confirm the ZIP backend needs no change — verified on `main` that a prefixed ZIP already opens with `format=ZIP`; the work is detection-side only
- [ ] 1.5 Non-seekable sources skip the probe and fall through; no attempt to buffer the whole stream to fake a seek

## 2. Widen the cue, validate the scan

- [ ] 2.1 Extend the prefix cue from `MZ`/ELF to also accept Mach-O magics and a `#!` shebang; keep the same `SFX_MAX` window and peek schedule
- [ ] 2.1a Red–green the Mach-O case specifically: a thin 64-bit stub (`cf fa ed fe`) plus an appended 7z currently returns `BROTLI` with a fabricated member on `sfx-format-detection`'s HEAD, where PE and ELF stubs open the real members. Assert the real members, not merely that no error is raised. Watch the two traps: `0xcafebabe` is also the Java class-file magic (a weak match on it would gate probes on every `.class` file — `sfx-format-detection`'s `design.md` flags this too), and a *fat* stub already fails loudly while only a *thin* one fails silently, so both need covering
- [ ] 2.2 Comment the cue as a **cost gate, not a correctness gate**, so the next reader does not re-derive it as a false-positive defence (a reviewer already did)
- [ ] 2.3 Validate a 7z hit: `StartHeaderCRC` over the 20-byte StartHeader, and `offset + 32 + NextHeaderOffset + NextHeaderSize <= source length`, preferring an exact end match when several candidates validate
- [ ] 2.4 Validate a RAR 5 hit via the main header's CRC32; RAR 4 via a parseable main header
- [ ] 2.5 Continue scanning past a candidate that fails validation rather than giving up
- [ ] 2.6 Add TAR's `ustar` to the scanned needles so script-wrapped tarballs resolve

## 3. Exhaustive scan and prefix reporting

- [ ] 3.1 Add the opt-in exhaustive scan to `open_archive` / `detect_format`, defaulting to off, reusing the same validation
- [ ] 3.2 Never enable it implicitly — no retry-after-failure, no extension-driven escalation
- [ ] 3.3 Add `prefix_kind` to `FormatInfo` (`NONE` / `EXECUTABLE` / `SCRIPT` / `OTHER_FORMAT` / `UNKNOWN`); classify `OTHER_FORMAT` by running the existing magic table against the prefix
- [ ] 3.4 Register the missing ZIP-family extensions (`.jar`, `.pyz`, `.whl`, `.apk`) — today not even the extension fallback rescues these

## 4. Verify

- [ ] 4.1 Red–green: `zipapp` output, a Spring Boot-style `#!/bin/sh` + ZIP, and a JPEG + appended ZIP all detect as `ZIP` and list their members. All three currently raise `FormatDetectionError` while opening fine with `format=ZIP` — assert the members, not just the absence of an error
- [ ] 4.2 Red–green: a makeself-style `#!/bin/sh` + tar.gz detects and opens
- [ ] 4.3 SFX matrix across stub kinds: PE, ELF (a real `rar a -sfx`), Mach-O, and shebang, for 7z and RAR
- [ ] 4.4 Scan validation: the 6 magic bytes embedded in unrelated data are not claimed; a 7z whose declared end overruns the source is not claimed; a 7z with trailing bytes appended still is
- [ ] 4.5 Non-seekable prefixed ZIP falls through rather than crashing or buffering the stream
- [ ] 4.6 Exhaustive scan: off by default leaves a beyond-window archive undetected and unread past the window; on, it finds it with `prefix_kind == UNKNOWN`
- [ ] 4.7 `prefix_kind` values for each fixture in 4.1–4.3
- [ ] 4.8 Cost regression: opening an ordinary non-archive file must not read more than the tail probe's bound plus the detection prefix — the whole point of the tiering
- [ ] 4.9 `./scripts/test.sh --all-configs` and `openspec validate --strict prefixed-archive-detection`
- [ ] 4.10 Archive this change in the finishing PR

## 5. Follow-ups

- [ ] 5.1 Write the ADR once this is applied: *detection cost is tiered by what the format guarantees* — stable, load-bearing, and currently blocked from ADR status only by the open question in `design.md`
- [ ] 5.2 Settle that open question with the maintainer's SFX corpus (old installers, media images): are there prefixed 7z/RAR that are not self-extracting executables? If so, widen the cue rather than abandon the tiering
- [ ] 5.3 Consider whether `prefix_kind == OTHER_FORMAT` deserves a diagnostic, so a caller sweeping a directory can notice polyglots without inspecting every result
- [ ] 5.4 Fix the stale cost comment in `sfx.py` (from #254): it says the geometric peeks cap the worst case "at a little over 2× the window". Counted, a full miss scans 64 + 256 + 1024 + 2048 KiB = 3392 KiB for a 2048 KiB window — **1.66×**. Small, but it is the number the tiering argument rests on, so it should be right where an implementer will read it
- [ ] 5.5 Make the forward scan resume instead of re-requesting the prefix. Each step calls `peek_more(step)` for the *first* `step` bytes, so the earlier bytes are paid for again — 1.66× rather than ~1×. The peek contract is why (detection must not move the cursor the backend will read from), but it is fixable per source kind: seek to `searched - overlap` and read the delta for `Path` and seekable streams; hand the scanner a `memoryview` over the buffer for `PeekableStream`, whose underlying I/O is already 1×. Not urgent — it narrows the tail-probe-vs-scan gap from ~53× to ~32×, so the tiering holds either way — but it is close to free and the scan is the expensive tier
