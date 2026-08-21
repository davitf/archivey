## 1. Give probes the source length

- [ ] 1.1 Extend the `StreamCodec` content-probe interface so a probe may consult the source length (e.g. `content_probe(prefix, *, source_length: int | None = None)`), defaulting to today's behaviour; leave the zlib and LZMA Alone probes unchanged in substance
- [ ] 1.2 Supply the length from `detect_format`: `os.path.getsize` for a path, end-relative seek for a seekable stream (restoring position per the stream-position contract), and the peek itself when it came back short — a file below `DETECTION_LIMIT` already reveals its exact size
- [ ] 1.3 Pass `None` for a non-seekable source longer than the peek; assert the probe then behaves exactly as before

## 2. The framing gate

- [ ] 2.1 Parse the first meta-block header per RFC 7932 §9.1–9.2 (WBITS, ISLAST/ISLASTEMPTY, MNIBBLES, MLEN, metadata skip, ISUNCOMPRESSED, pad-to-byte-boundary) — enough to recover `(outcome, consumed_bytes, declared_length)`, no Huffman decoding
- [ ] 2.2 Reject when a declared meta-block overruns the source; keep today's answer when the outcome is compressed or the length is unknown
- [ ] 2.2a Apply the same invariant to the LZMA Alone probe: reject a source no longer than its 13-byte header, which carries no range-coder payload. That is the entire measured real-world Alone residual — 4 files of 40 000, all exactly 13 bytes (`cryptography\n`, `launchpadlib\n`, `deb.sury.org\n`), accepted because the decoder runs out of input and `TruncatedError` counts as a match
- [ ] 2.3 Chain-walk form: follow byte-aligned self-describing meta-blocks, bounded in link count, stopping at the first compressed block; reject a link that overruns or a declared end with trailing bytes
- [ ] 2.4 Confirm the gate costs no decompression and at most a bounded number of small reads

## 3. Confidence and error provenance

- [ ] 3.1 Report `GUESS` for a Brotli content-probe match with no corroborating extension, `PROBABLE` when the extension agrees; zlib and LZMA Alone keep `PROBABLE` unconditionally. Check the interaction with `EXTENSION_FORMAT_UNCONFIRMED`, which keys on `detected_by="extension"` and should not start double-reporting
- [ ] 3.1a Optionally keep `PROBABLE` for a probe-only Brotli hit whose **first meta-block is compressed**: measured 0.014% acceptance on random data against ~100% for uncompressed-first, and 25/25 real-world streams are compressed-first. Note this is a *refinement* of the gate, not a substitute — the gate already separates real incompressible streams (declared length fits, verified at 4 KiB / 64 KiB / 1 MiB) from fabricated ones (96.7% overrun the source), and it keeps the genuine uncompressed-first `.br` files that a class-based downgrade would penalise
- [ ] 3.2 A read failure on a probe-only (`GUESS`) single-file result names the unconfirmed identification instead of presenting as a truncation; a corroborated result keeps today's error
- [ ] 3.3 Do not refuse the open on probe-only evidence — a clean read stays a success

## 4. Verify

- [ ] 4.1 Real-stream corpus: qualities 0/1/5/9/11 × `lgwin` 10/22/24 × payloads from empty to 1 MiB, including the incompressible cases whose first meta-block is uncompressed. **Zero** false negatives is the binding constraint
- [ ] 4.2 Red–green false positives: `MZ` + `\x90`×4094, a `/**\n` C header, and a random-blob sweep; assert `FormatDetectionError` rather than a fabricated member
- [ ] 4.3 Regression for the residual, with the *named* families rather than only a random blob whose MLEN fits: an **OLE/CFB** fixture (`D0 CF 11 E0 A1 B1 1A E1` + ≥ 7425 bytes — its constant magic always declares MLEN 7422, so it always survives) and a **COFF** object. Assert they still detect as Brotli after the gate: it is sound, not exhaustive
- [ ] 4.4 Regression for partial output: a source whose first uncompressed meta-block fits and whose *next* header is invalid delivers a full buffer (65 536 bytes measured) of verbatim input bytes before raising. Pin that bytes-then-error shape so the error-provenance wording in 3.2 cannot regress into claiming nothing was produced
- [ ] 4.5 Non-seekable source of unknown length keeps today's behaviour
- [ ] 4.6 Confidence: bare Brotli stream → `GUESS`; `x.br` → `PROBABLE`; both still `BROTLI`
- [ ] 4.7 `./scripts/test.sh --all-configs` (the Brotli extra is optional — the probe must stay skipped, not crash, when it is absent)
- [ ] 4.8 `openspec validate --strict brotli-probe-framing-gate`
- [ ] 4.9 Re-scope `dev-docs/open-issues.md` P12 and `dev-docs/threat-model.md` O10 to three clauses, not one: the listing is wrong; a full read raises; **and a prefix of fabricated bytes may already have been produced** (65 536 bytes measured — see results doc §5.1). The registered "silent wrong answer" is overstated, but so was the first correction to it: "every read failed" describes the terminal exception, not the bytes delivered. Residual after this change is ~0.035% of a real filesystem
- [ ] 4.10 Archive this change in the finishing PR (`openspec archive brotli-probe-framing-gate --yes`)

## 5. Follow-ups (explicitly not in this change)

- [ ] 5.1 Extension-first detection ordering: try formats matching the extension before the rest, falling back on a miss or when there is no filename. Better than the status quo *and* than a hard extension gate, but it restructures `_detect_format_body` for every format — its own proposal
- [ ] 5.2 ~~Field survey of `e_lfanew` maxima and real-world WBITS on Windows/macOS~~ — **done**, see results doc §7.2. Two consequences below
- [ ] 5.3 **The executable cue is blind on macOS — now confirmed from both directions and reproduced.** `executable_cue` handles `MZ` and ELF only, so a Mach-O SFX stub gets no cue, while `cf fa ed fe` (`MH_MAGIC_64`, little-endian) is *structurally guaranteed* to parse as an uncompressed meta-block header (200 real hits on a macOS runner). `sfx-format-detection` found the same gap independently at `34db1b0` via a macOS CI failure and pinned it with a test, but its note says the stub "falls through to the content probes" — measured against that very commit, it does not merely fall through: PE and ELF stubs open the real 7z members, and a Mach-O stub returns `BROTLI` with one fabricated `.uncompressed` member. The defect that change exists to close survives on macOS. Fixed by `prefixed-archive-detection` (PR #257), which widens the cue; see results doc §7.2 for the reproduction and the thin-versus-fat nuance
- [ ] 5.4 Revisit the `e_lfanew` bound in `sfx-format-detection` — **but scoped to SFX stubs** (maintainer ruling, results doc §7.3). 12 887 Windows PEs give a maximum of 11 648, from `tcblaunch.exe`, which is not and will never be a self-extracting archive. The general-PE counterexamples (EFI kernel image, `.winmd` metadata assemblies) do not constrain a cue aimed at stubs. If a bound is wanted for read-size reasons, exceeding it should mean "cannot confirm cheaply", not "not an executable"
- [ ] 5.5 Build a real SFX corpus (maintainer, later): generate stubs with current *and* old tools (WinRAR, 7-Zip, installer-era self-extractors) and pull from old installation archives and media images. That is the only way to learn what a 16-bit NE/LE self-extractor actually looks like; until it exists, §3.1/§7.2's executable-header conclusions are bounds on the general PE population, not on the stub population
- [ ] 5.6 Delete `.github/workflows/brotli-field-survey.yml` once §7.2 is settled — it is explicitly temporary and must not become part of the ordinary CI signal
