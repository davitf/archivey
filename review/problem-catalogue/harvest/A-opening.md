# Harvest A — Opening, detection, sources

Bounded drop from Worker A verification (Topic 8 pass 1). Neutral phrasing; no
investigation beyond what the claim rows already touched.

---

### 1. Docs sample passes `FormatInfo` where an `ArchiveFormat` is required

- **Problem:** A published snippet calls a helper with the wrong object type, so
  copy-paste fails immediately.
- **Symptom:** `ArchiveyUsageError: format_availability() takes an ArchiveFormat,
  but got FormatInfo(...)`.
- **Evidence:** `docs/opening-and-listing.md:74-81`; runtime repro; A-18.
- **Today:** Callers must pass `detect_format(path).format` (or an
  `ArchiveFormat` constant).

### 2. Access-cost page understates which formats always need seek

- **Problem:** One guide page lists fewer “must seek” formats than another page
  (and than `required_source`).
- **Symptom:** Reader may think 7z/RAR can be opened from a pure pipe under
  `streaming=True`.
- **Evidence:** `docs/access-and-cost.md:145-146` vs `docs/opening-and-listing.md:66-68`;
  `format_availability` SEEKABLE for ZIP/7z/RAR/ISO; A-16.
- **Today:** Opening 7z/RAR from a non-seekable source raises
  `StreamNotSeekableError` even with `streaming=True`.

### 3. Absolute “never buffers” wording on non-seekable open

- **Problem:** Fail-fast / no-silent-buffer language is written as universal, but
  the no-buffer rule is scoped (pipe / ADR 0010), and some seekable-stream paths
  differ.
- **Symptom:** Absolute reading of opening/philosophy/migrating prose conflicts
  with other documented cases (E-71).
- **Evidence:** A-6 coordinator verdict; `docs/opening-and-listing.md:25-28`,
  `docs/philosophy.md:42`, `docs/migrating.md:170-172`.

### 4. Two exception type names for “optional piece missing”

- **Problem:** Guide pages name different exceptions when something optional is
  absent, without saying they are different failure points.
- **Symptom:** Readers treat `PackageNotInstalledError` and
  `UnsupportedFormatError` as competing names for one case (A-33 false conflict).
- **Evidence:** `docs/formats.md:36-37`, `:58-59` vs
  `docs/opening-and-listing.md:128-130`; registry NONE → `UnsupportedFormatError`;
  codec/AES path → `PackageNotInstalledError`.
- **Today:** Format-level NONE at open vs member/decrypt backend absence.

### 5. Self-extracting archives claimed under Detection, but sniff does not find them

- **Problem:** Docs say SFX stubs are detected when payload follows an executable
  header; auto detection does not.
- **Symptom:** `detect_format` / auto `open_archive` on `MZ` + RAR/7z payload →
  `FormatDetectionError`; forced `format=RAR` can still open.
- **Evidence:** `docs/formats.md:225-226`; `format-detection` SFX requirement;
  `src/archivey/internal/detection.py` (SFX deferred); A-34 repro.
- **Today:** RAR parser scans for magic when the format is already RAR.

### 6. Extension-conflict example uses a non-archive extension

- **Problem:** Guide illustrates `FORMAT_EXTENSION_CONFLICT` with a `.jpg` that is
  really a ZIP.
- **Symptom:** No conflict diagnostic (`.jpg` is not a mapped archive extension);
  only magic win.
- **Evidence:** `docs/opening-and-listing.md:120-123`; A-29 spot-check (conflict on
  `.tar`/`.7z`/…, not on `.jpg`).

### 7. Detection section duplicated on Formats

- **Problem:** Magic-first + confidence is explained twice.
- **Symptom:** Drift risk (already seen with seek lists / exception names).
- **Evidence:** `docs/formats.md:222-228` vs `docs/opening-and-listing.md:109-131`;
  A-35.

### 8. Old RAR `.rNN` “lone file” wording vs open failure

- **Problem:** Guide says a `.rNN` alone is read as a lone file when `.rar` is
  missing.
- **Symptom:** Discovery correctly refuses to build a set; opening a real
  continuation volume then raises `UnsupportedFeatureError` (need first volume),
  not a successful lone listing.
- **Evidence:** `docs/opening-and-listing.md:99-101`; `volumes.py` comments;
  `tests/test_volumes.py::test_discover_rnn_without_first_volume_is_not_a_set`;
  A-23 open of `tinyvol_rnn.r00` alone.
- **Today:** Discovery = not a set; parser still demands volume 1 for true
  multi-vol bytes.

### 9. Inner-TAR / extras dependence only spelled on Opening

- **Problem:** Detection outcome for compressed tar vs bare compressor changes
  with optional packages; only one page walks that wrinkle.
- **Symptom:** `[core-only]` (or missing zstd) readers get bare compressor +
  install-hint error at open; easy to miss if only Formats is read.
- **Evidence:** `docs/opening-and-listing.md:126-131`; A-31/A-32;
  `test_inner_tar_probe_skipped_when_codec_missing`.

### 10. Password confirmation cost emphasized for 7z on Opening, ZipCrypto on Access

- **Problem:** “Wrong candidates cost work” is split across pages with different
  headline formats (7z KDF vs ZipCrypto STORED).
- **Symptom:** Not false, but easy to under-weight the STORED ZipCrypto niche if
  only Opening is read (or vice versa).
- **Evidence:** `docs/opening-and-listing.md:140-141`;
  `docs/access-and-cost.md:154-158`; A-37.
