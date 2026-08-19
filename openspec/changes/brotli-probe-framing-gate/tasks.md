## 1. Give probes the source length

- [ ] 1.1 Extend the `StreamCodec` content-probe interface so a probe may consult the source length (e.g. `content_probe(prefix, *, source_length: int | None = None)`), defaulting to today's behaviour; leave the zlib and LZMA Alone probes unchanged in substance
- [ ] 1.2 Supply the length from `detect_format`: `os.path.getsize` for a path, end-relative seek for a seekable stream (restoring position per the stream-position contract), and the peek itself when it came back short — a file below `DETECTION_LIMIT` already reveals its exact size
- [ ] 1.3 Pass `None` for a non-seekable source longer than the peek; assert the probe then behaves exactly as before

## 2. The framing gate

- [ ] 2.1 Parse the first meta-block header per RFC 7932 §9.1–9.2 (WBITS, ISLAST/ISLASTEMPTY, MNIBBLES, MLEN, metadata skip, ISUNCOMPRESSED, pad-to-byte-boundary) — enough to recover `(outcome, consumed_bytes, declared_length)`, no Huffman decoding
- [ ] 2.2 Reject when a declared meta-block overruns the source; keep today's answer when the outcome is compressed or the length is unknown
- [ ] 2.3 Chain-walk form: follow byte-aligned self-describing meta-blocks, bounded in link count, stopping at the first compressed block; reject a link that overruns or a declared end with trailing bytes
- [ ] 2.4 Confirm the gate costs no decompression and at most a bounded number of small reads

## 3. Confidence and error provenance

- [ ] 3.1 Report `GUESS` for a content-probe match with no corroborating extension, `PROBABLE` when the extension agrees; check the interaction with `EXTENSION_FORMAT_UNCONFIRMED`, which keys on `detected_by="extension"` and should not start double-reporting
- [ ] 3.2 A read failure on a probe-only (`GUESS`) single-file result names the unconfirmed identification instead of presenting as a truncation; a corroborated result keeps today's error
- [ ] 3.3 Do not refuse the open on probe-only evidence — a clean read stays a success

## 4. Verify

- [ ] 4.1 Real-stream corpus: qualities 0/1/5/9/11 × `lgwin` 10/22/24 × payloads from empty to 1 MiB, including the incompressible cases whose first meta-block is uncompressed. **Zero** false negatives is the binding constraint
- [ ] 4.2 Red–green false positives: `MZ` + `\x90`×4094, a `/**\n` C header, and a random-blob sweep; assert `FormatDetectionError` rather than a fabricated member
- [ ] 4.3 Regression for the residual: a blob whose declared block genuinely fits still detects as Brotli — the gate is sound, not exhaustive
- [ ] 4.4 Non-seekable source of unknown length keeps today's behaviour
- [ ] 4.5 Confidence: bare Brotli stream → `GUESS`; `x.br` → `PROBABLE`; both still `BROTLI`
- [ ] 4.6 `./scripts/test.sh --all-configs` (the Brotli extra is optional — the probe must stay skipped, not crash, when it is absent)
- [ ] 4.7 `openspec validate --strict brotli-probe-framing-gate`
- [ ] 4.8 Re-scope `dev-docs/open-issues.md` P12 and `dev-docs/threat-model.md` O10: the investigation measured the registered "silent wrong answer" as overstated — the listing is wrong, every read fails — and the residual after this change is ~0.035% of a real filesystem
- [ ] 4.9 Archive this change in the finishing PR (`openspec archive brotli-probe-framing-gate --yes`)

## 5. Follow-ups (explicitly not in this change)

- [ ] 5.1 Extension-first detection ordering: try formats matching the extension before the rest, falling back on a miss or when there is no filename. Better than the status quo *and* than a hard extension gate, but it restructures `_detect_format_body` for every format — its own proposal
- [ ] 5.2 ~~Field survey of `e_lfanew` maxima and real-world WBITS on Windows/macOS~~ — **done**, see results doc §7.2. Two consequences below
- [ ] 5.3 **The executable cue is blind on macOS.** `executable_cue` handles `MZ` and ELF only, so a Mach-O SFX stub gets no cue — while `cf fa ed fe` (`MH_MAGIC_64`, little-endian) is *structurally guaranteed* to pass the Brotli probe as an uncompressed meta-block header (200 real hits on a macOS runner). That is the `sfx-format-detection` defect on a platform that change never considered. The framing gate happens to rescue it, but the missing cue belongs in its own proposal against `format-detection`
- [ ] 5.4 Revisit the `e_lfanew` bound in `sfx-format-detection`: 12 887 Windows PEs give a maximum of **11 648**, so any cap ≤ 1024 rejects a real binary. If a bound is wanted for read-size reasons, exceeding it should mean "cannot confirm cheaply", not "not an executable"
- [ ] 5.5 Delete `.github/workflows/brotli-field-survey.yml` once §7.2 is settled — it is explicitly temporary and must not become part of the ordinary CI signal
