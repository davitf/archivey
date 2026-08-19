## 1. Detection SFX scan

- [ ] 1.1 Add bounded SFX magic scan (`MZ` / ELF prefix → RAR/7z needles) in `detect_format`, setting `payload_offset` and `detected_by`
- [ ] 1.2 Run the SFX scan before content probes when the prefix looks executable (no silent Brotli claim)
- [ ] 1.3 Share or align the window size with `rar_parser.SFX_MAX` (document the chosen bound)

## 2. Open-path hand-off

- [ ] 2.1 Thread non-zero `payload_offset` from detection through `open_archive` into backend open via explicit start-offset argument or bounded offset view (not bare seek alone)
- [ ] 2.2 Confirm RAR auto-open of SFX succeeds with real members (no fabricated single-file member)
- [ ] 2.3 Gate 7z auto-open SFX on `sevenz-sfx-start-offset` (or land that change in the same train)

## 3. Verify

- [ ] 3.1 Red–green: low-entropy `MZ` stub + RAR payload must not detect/open as Brotli; assert real RAR members
- [ ] 3.2 Varied-stub + RAR/7z cases from the SFX matrix (match / no-match / extension fallthrough)
- [ ] 3.3 Bare brotli / non-executable streams unchanged
- [ ] 3.4 `openspec validate --strict sfx-format-detection`
- [ ] 3.5 Archive this change in the finishing PR (`openspec archive sfx-format-detection --yes`)
