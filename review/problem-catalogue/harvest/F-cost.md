# Harvest F — Cost, accelerators, measurement

Bounded drop from Worker F verification (Topic 8 pass 1). Neutral phrasing; no
investigation beyond what the claim rows already touched.

---

### 1. Solid “do this” sample ignores `stream is None`

- **Problem:** Published solid-archive loop calls `consume(stream)` on every
  `stream_members()` yield.
- **Symptom:** `AttributeError: 'NoneType' object has no attribute 'read'` on
  archives with directory (or other non-file) members.
- **Evidence:** `docs/access-and-cost.md:71-74`; `archive-reading` Non-file
  stream_members yield None; F-16 repro on `tests/fixtures/rar/basic_solid__.rar`.
- **Today:** Non-file members correctly yield `None`; callers must skip or branch.

### 2. AUTO rapidgzip gate overstated as seek + size only

- **Problem:** Cost page says AUTO selects only when seekability is declared and
  known compressed size ≥ 1 MiB.
- **Symptom:** Readers think unknown size never accelerates; also silent on the
  verifiable decompressed-size gate for DEFLATE AUTO.
- **Evidence:** `docs/access-and-cost.md:117-120`; `seekable-decompressor-streams`
  AUTO threshold (unknown size keeps pre-threshold AUTO); `config.py`
  `AcceleratorMode` / `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`; F-26.
- **Today:** Unknown size + declared seek + package present can still select AUTO.

### 3. CostReceipt table omits `notes`

- **Problem:** Four-row field table lists listing/access/stream/solid only.
- **Symptom:** A verbatim → DS promotion would drop a public field.
- **Evidence:** `docs/access-and-cost.md:40-44`; `access-mode-and-cost` CostReceipt
  includes `notes: tuple[str, ...]`; F-7.
- **Today:** Spec and dataclass carry `notes`; guide table does not.

### 4. Two distinct 1 MiB constants; rewind threshold unnamed on the cost page

- **Problem:** “About a megabyte” for `STREAM_REWIND_REDECOMPRESSES` is
  `REWIND_REDECODE_WARN_BYTES`; the page’s named constant is
  `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` (compressed-input AUTO gate).
- **Symptom:** Readers conflate accelerator AUTO selection with rewind diagnostics.
- **Evidence:** `docs/access-and-cost.md:100-102`, `:119`; `config.py` both constants;
  F-22.
- **Today:** Same numeric value, different quantities (decompressed redecode distance vs
  compressed input size).

### 5. Silence: `ON` raises when rapidgzip absent; `AUTO` falls back

- **Problem:** No guide page states the absence-path difference for
  `AcceleratorMode`.
- **Symptom:** Callers who set `ON` without `[seekable]` get
  `PackageNotInstalledError`; `AUTO` stays quiet on stdlib.
- **Evidence:** F-29; `config.py` AcceleratorMode; codecs raise when ON and package
  missing; monkeypatch absence under **`[all]`**.
- **Today:** Behaviour matches config docstring; guide silence remains.

### 6. Silence: `enable_measurement()` is opt-in / open-scoped

- **Problem:** No guide page states that `reader.io_stats()` is `None` outside
  `enable_measurement()`.
- **Symptom:** Callers looking for always-on counters see `None` and have no
  guide pointer.
- **Evidence:** F-39; `src/archivey/measurement.py`; spot-check without/with context.
- **Today:** Docstring + `api.md` mkdocstrings only.

### 7. Gotchas states accelerator containment without the path residual

- **Problem:** Gotchas says closing a source under a live accelerator is a clean
  failure, not a crash; it does not mention the residual path-source
  truncation/CRC `std::terminate`.
- **Symptom:** Pair incompleteness of the kind #223 round-2 flagged — cost page
  has both halves; gotchas only the positive half.
- **Evidence:** `docs/gotchas.md:45-48` vs `docs/access-and-cost.md:167-177`;
  F-33 + F-35; `dev-docs/known-issues.md` Bug 3 residual.
- **Today:** Cost page still states both; keep them paired on edit.

### 8. `known-issues.md` Bug 3 mitigation text lags `_TrappingSource`

- **Problem:** Known-issues still frames caller-close as “can only be fixed
  upstream” for the Python-source-raises abort.
- **Symptom:** Maintainer doc contradicts shipped containment +
  `tests/test_accelerator_bug3_trap.py`.
- **Evidence:** `dev-docs/known-issues.md` Bug 3; `codecs.py` `_TrappingSource`;
  F-33 / F-34 (tests green under **`[all]`**).
- **Today:** Spec requires containment; residual path truncations/CRC remain
  upstream (F-35).

### 9. Stale nightly URL still `archivey-2` (cite O-4 only)

- **Problem:** User-facing nightly link uses the pre-rename repo name.
- **Symptom:** Wrong name on a published page (GitHub redirects).
- **Evidence:** `docs/access-and-cost.md:17-18`; O-4 / F-3 / S-1.
- **Today:** Do not re-file; fix with the O-4 item.

### 10. Cross A-16: seek sentence on the same cost page

- **Problem:** Non-seekable section still names only ZIP+ISO as always needing
  seek (Worker A).
- **Symptom:** Implies 7z/RAR pipe-ok under `streaming=True`.
- **Evidence:** `docs/access-and-cost.md:145-146`; A-16; session
  `format_availability` SEEKABLE for ZIP/7z/RAR/ISO.
- **Today:** Already harvested under A; noted here because it sits on the F page.

---

## Process notes

- Prefer spec over `src/` (O-26); Settles-it line numbers drifted.
- `[code]` F-2 / F-16 / F-17 executed; F-34 tests run.
- Nightly F-5 numbers checked against downloaded `benchmark-wall-realistic`
  artifact for run `29992136861`.
- No `docs/` or `src/` edits.
