## Why

The Brotli content probe accepts data that is not Brotli, at a rate that matters:
**8.2% of arbitrary binary data**, and — measured on a real filesystem — **3.5% of 39 859
files under `/usr`**, dominated by the Doxygen comment opener `/**\n`.
`detect_format("/usr/include/lzma.h")` returns `ArchiveFormat.BROTLI` at `PROBABLE`
confidence; `open_archive` then lists one fabricated `lzma.h.uncompressed` member and the
read fails with `TruncatedError`, blaming the file for being truncated and naming a format
it never was.

`dev-docs/investigations/brotli-content-probe-results.md` (the deep dive requested by
`sfx-format-detection` and registered as `open-issues.md` P12 / `threat-model.md` O10)
establishes the cause and the fix:

- The mechanism is RFC 7932's **non-last uncompressed meta-block** — a four-byte header
  with ~15 constrained bits, after which the decoder copies literal bytes. Analytic
  prediction 8.10%, measured 8.16%.
- **Probe tuning cannot fix it.** Both accepting paths *declare* a length and emit no
  evidence, so a longer prefix supplies more bytes to copy and demanding output tests
  something the uncompressed path already passes. Every output-demanding variant trades
  false positives for false negatives roughly one-for-one (24–90 misses of 150 real
  streams).
- **The missing information is the file size.** A declared uncompressed/metadata
  meta-block asserts bytes the file must physically contain, so
  `header + declared_length <= file_size` is an *invariant of a complete valid file*, not a
  heuristic. It cuts false positives 25× (4 KiB) to 162× (chain-walk form) with **0/150**
  false negatives on real streams — 56 of which take the uncompressed path themselves.

The residual is not a silent wrong answer (across 364 false positives, every read failed),
so this is a **wrong-answer-then-wrong-error** defect rather than data fabrication. Both
halves are worth fixing.

## What Changes

- **Gate the Brotli probe on the framing invariant.** Before accepting, verify that a
  first meta-block declaring a length fits inside the source; in the chain-walk form,
  follow byte-aligned self-describing meta-blocks until the first compressed one. Sound by
  construction: a complete valid Brotli file cannot fail it.
- **Give the probe the source size it needs.** `content_probe(prefix) -> bool` cannot see
  it. Extend the codec-probe interface so a probe may consult the source length when
  detection knows it (path, seekable stream, or a short peek that reveals the whole file),
  and degrade to today's behaviour when it does not.
- **Report probe-only single-file results as `GUESS`, not `PROBABLE`.** A content probe
  with no corroborating extension is not "probable" evidence for a magic-less format;
  probe + matching extension stays `PROBABLE`. This narrows the existing content-probe
  requirement's confidence table for Brotli.
- **Make the failure honest.** When a read fails on a single-file result whose only
  evidence was a content probe, the error SHALL say the format identification was
  unconfirmed rather than presenting as a plain truncation.
- **No** disabling of content probes, **no** extension gate, **no** new config knob — see
  Decisions below.

## Capabilities

### New Capabilities

### Modified Capabilities

- `format-detection` — the magic-less content-probe requirement gains a normative framing
  gate for Brotli, and probe-only results for magic-less single-file formats report
  `GUESS` rather than `PROBABLE` unless the extension corroborates.
- `compressed-streams` — the content-probe interface may consult the source length;
  probes that do not need it (zlib, LZMA Alone) are unaffected.
- `error-handling` — a read failure on a probe-only single-file result names the weak
  provenance instead of presenting as a truncation.

## Decisions

Recorded because they were considered and rejected on measurement, not overlooked
(results doc §7.1):

- **Not disabled by default, and not gated on an archive-like extension.** `VISION.md`'s
  founding use case is a backup corpus where "wrong extensions are normal"; both levers
  remove exactly the discovery it calls for. Disabling would also make a genuine `.br`
  file report *less* confidence than its bytes support — with the probe off, `.br` still
  detects via the extension guess, at `GUESS`.
- **No `disable_brotli_probe` config field.** After the gate the real-world rate is
  0.035%, which does not earn the API surface, documentation and test matrix. If a knob is
  ever wanted it should be a general "content probes off" strictness setting, not a
  per-format special case.
- **No WBITS whitelist.** The encoder emits exactly the requested `lgwin` at quality ≥ 2,
  and real files use the range: the two `.br` files on the measurement system are WBITS 15
  and 16, and twelve WOFF2 fonts split 19/22. Whitelisting the observed union is worth
  2.1×, against 25–162× for the sound gate.
- **Extension-first detection ordering is out of scope.** Trying the formats matching the
  extension first and falling back to the rest is better than either the status quo or a
  hard gate — it uses the extension as a priority order rather than a filter — but it
  restructures `_detect_format_body` for every format. It belongs in its own change.

## Impact

- Modules: `src/archivey/internal/streams/codecs.py` (`BrotliCodec.content_probe`, the
  `StreamCodec` probe signature), `src/archivey/internal/detection.py` (supplying the
  source length; confidence for probe-only results), the single-file read path for the
  error message.
- Public API: `detect_format` on a bare Brotli stream reports `GUESS` where it reported
  `PROBABLE`; `.br` files are unaffected. Behaviour on the ~8% of junk that used to detect
  as `BROTLI` changes to `FormatDetectionError`, which is the point.
- Tests: the 150-stream real-Brotli corpus (qualities 0/1/5/9/11 × `lgwin` 10/22/24 ×
  payloads from empty to 1 MiB) must stay at zero false negatives; `/**\n`, `MZ` +
  `\x90`×4094, and random-blob regressions on the false-positive side.
- Docs: `docs/formats.md` detection prose; the confidence table in
  `openspec/specs/format-detection/spec.md`.
- Related: closes the deep-dive obligation from `sfx-format-detection`; lets
  `open-issues.md` P12 and `threat-model.md` O10 be re-scoped (the investigation found the
  registered "silent wrong answer" wording overstated — listing is wrong, every read
  fails).
