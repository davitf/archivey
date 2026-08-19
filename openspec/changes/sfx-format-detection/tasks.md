## 1. Detection SFX scan + probe differentiation

- [ ] 1.0 Investigate Brotli/content-probe vs executable-shaped prefixes (stronger PE/ELF/SFX cues; larger/stricter `_PROBE_PREFIX` / `TruncatedError` handling; scan-first-then-probe; hybrids). Record chosen rule + FP/FN trade-offs in `design.md`. Fixture: real Brotli whose prefix coincides with a weak executable cue must still detect as Brotli (or document accepted residual).
- [ ] 1.1 Add bounded SFX magic scan (`MZ` / ELF / refined cues → RAR / 7z / ZIP `PK\x03\x04` needles) in `detect_format`, setting `payload_offset` and `detected_by`
- [ ] 1.2 Apply the investigated probe policy so SFX stubs cannot silently become Brotli fabricated members, without hard-disabling Brotli on bare `MZ`
- [ ] 1.3 Promote `rar_parser.SFX_MAX` to one shared constant (2 MiB) used by RAR parser, detection, and 7z forced-format scan; import it from detection (do not keep a second copy)

## 2. Open-path hand-off

- [ ] 2.1 Thread non-zero `payload_offset` from detection through `open_archive` into backend open via explicit start-offset argument or bounded offset view (not bare seek alone)
- [ ] 2.2 Confirm RAR and ZIP auto-open of SFX succeeds with real members (no fabricated single-file member)
- [ ] 2.3 Gate 7z auto-open SFX on `sevenz-sfx-start-offset` (or land that change in the same train)

## 3. Verify

- [ ] 3.1 Red–green: low-entropy `MZ` stub + RAR payload must not detect/open as Brotli; assert real RAR members
- [ ] 3.2 Red–green: low-entropy `MZ` stub + ZIP payload must not detect/open as Brotli; assert real ZIP members
- [ ] 3.3 Real Brotli (and bare non-executable streams) still detect; include a weak-executable-prefix Brotli case from 1.0
- [ ] 3.4 Varied-stub + RAR/7z/ZIP cases from the SFX matrix (match / no-match / extension fallthrough)
- [ ] 3.5 `openspec validate --strict sfx-format-detection`
- [ ] 3.6 Archive this change in the finishing PR (`openspec archive sfx-format-detection --yes`)
