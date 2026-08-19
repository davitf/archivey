## 1. Detection SFX scan + probe differentiation

- [x] 1.0 Investigate Brotli/content-probe vs executable-shaped prefixes. *Measured and recorded in `design.md` §Investigation result: probe tuning is a dead end (4096-byte prefix leaves FP at 8.13%; requiring output loses 10/15 real streams), and the A-34 stub is synthetic (real PE/ELF stubs and 887 real ELF binaries are not probe hits). Landed rule: scan-first on a weak cue, probes suppressed only on a structurally validated PE/ELF. The weak-cue Brotli fixture is `test_a_weak_cue_still_lets_a_content_probe_answer`; the residual (~8% of arbitrary data probes as Brotli) is registered as P12 / O10 with a deep-dive brief.*
- [x] 1.1 Add bounded SFX magic scan (`MZ` / ELF / refined cues → RAR / 7z / ZIP `PK\x03\x04` needles) in `detect_format`, setting `payload_offset` and `detected_by`
- [x] 1.2 Apply the investigated probe policy so SFX stubs cannot silently become Brotli fabricated members, without hard-disabling Brotli on bare `MZ`
- [x] 1.3 Promote `rar_parser.SFX_MAX` to one shared constant (2 MiB) used by RAR parser, detection, and 7z forced-format scan. *Landed in `internal/sfx.py` rather than `detection.py` (maintainer): a backend importing the detector would be a new edge. Nothing outside `rar_parser` imported the old name, so no re-export.*

## 2. Open-path hand-off

- [x] 2.1 Thread non-zero `payload_offset` from detection through `open_archive` into backend open via explicit start-offset argument or bounded offset view (not bare seek alone)
- [x] 2.2 Confirm RAR and ZIP auto-open of SFX succeeds with real members (no fabricated single-file member)
- [x] 2.3 Gate 7z auto-open SFX on `sevenz-sfx-start-offset`. *Landed first in the same PR, so no gate was needed: 7z SFX auto-opens end to end.*

## 3. Verify

- [x] 3.1 Red–green: low-entropy `MZ` stub + RAR payload must not detect/open as Brotli; assert real RAR members
- [x] 3.2 Red–green: low-entropy `MZ` stub + ZIP payload must not detect/open as Brotli; assert real ZIP members
- [x] 3.3 Real Brotli (and bare non-executable streams) still detect; include a weak-executable-prefix Brotli case from 1.0
- [x] 3.4 Varied-stub + RAR/7z/ZIP cases from the SFX matrix (match / no-match / extension fallthrough)
- [ ] 3.5 `openspec validate --strict sfx-format-detection`
- [ ] 3.6 Archive this change in the finishing PR (`openspec archive sfx-format-detection --yes`)
