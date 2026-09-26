# Harvest E — Formats, codecs, stored digests

Bounded drop from Worker E verification (Topic 8 pass 1). Neutral phrasing; no
investigation beyond what the claim rows already touched.

---

### 1. Quick matrix calls Directory listing “indexed”

- **Problem:** The formats quick matrix uses the same “indexed” word for Directory as
  for ZIP/ISO/7z, but the directory backend has no O(1) index.
- **Symptom:** Readers expect CD-style listing cost; actual `cost.listing_cost` is
  `REQUIRES_SCANNING` (tree walk).
- **Evidence:** `docs/formats.md:13`; `format-directory` Present a filesystem
  directory as an ArchiveReader; E-1 spot-check on a temp tree.
- **Today:** Access remains `DIRECT`; only the Listing cell is wrong.

### 2. Silence: RAR from a seekable stream spills the whole archive to temp

- **Problem:** No guide page states that non-direct RAR member reads from a stream
  source materialize the entire archive to a temp `.rar`.
- **Symptom:** Cost and diagnostics look identical to a path open; disk use is
  unbounded by archive size; stored members skip the spill (`_can_direct_read`).
- **Evidence:** E-71 coordinator verdict; `rar_reader._ensure_archive_path`; P11 in
  `dev-docs/open-issues.md`; cross A-6 absolute “never buffers” wording.
- **Today:** Behavior works; honesty channel is empty.

### 3. `formats.md` §ISO silent on the process-global pycdlib patch

- **Problem:** Gotchas documents the hang-safety `deque` patch and links to
  `formats.md#iso-9660`, but that section never mentions it.
- **Symptom:** Scope row 10 is a link with no landing; callers using pycdlib in the
  same process only learn the process-global effect from gotchas.
- **Evidence:** `docs/gotchas.md:87-90`; `docs/formats.md:127-133`;
  `iso_reader._install_pycdlib_directory_cycle_guard`; E-48.
- **Today:** Patch installs on `import archivey` when pycdlib is importable.

### 4. Access-cost seek sentence still understates 7z/RAR (cross A-16)

- **Problem:** Formats/ZIP/ISO claims co-cite `access-and-cost.md:145-146`, which
  names only ZIP and ISO as always needing seek.
- **Symptom:** Implies 7z/RAR might open from a pipe under `streaming=True`.
- **Evidence:** A-16 coordinator verdict; E-4 / E-45 co-cites; session
  `format_availability` → `SEEKABLE` for ZIP, 7z, RAR, ISO.
- **Today:** Opening those four from NonSeek raises `StreamNotSeekableError` even
  with `streaming=True`.

### 5. Rapidgzip bare-gzip truncation caveat must stay sharp

- **Problem:** Highest-stakes negative safety claim in this cluster (O-2): with
  `[seekable]` rapidgzip on seekable `.gz`, truncation detection is best-effort.
- **Symptom:** Softening the caveat or omitting `use_rapidgzip=OFF` is O-16’s
  failure mode; ZIP/7z member CRC paths must not be conflated with bare streams.
- **Evidence:** `docs/formats.md:148-153`; `docs/gotchas.md:80-84`;
  `seekable-decompressor-streams` Accelerator errors translate uniformly; E-53 /
  E-54.
- **Today:** Guide + gotchas agree; keep both aligned on edit.

### 6. Exception names for “optional piece missing” (cross A-33)

- **Problem:** Formats pages correctly name `PackageNotInstalledError` for missing
  ZIP/7z codecs and WinZip AES; opening-page format NONE uses
  `UnsupportedFormatError`.
- **Symptom:** Readers may treat the two names as competing labels for one case.
- **Evidence:** E-5 / E-14; A-33 false-conflict resolution; registry NONE vs
  shared-codec absence.
- **Today:** Two situations, two exceptions — keep homes distinct.

### 7. RAR password-on-stdin not in `format-rar` openspec

- **Problem:** Guide states bare `-p` with secret on stdin; code does that;
  `format-rar` has no requirement naming the stdin feed (Settles-it pointed at
  the member-path argv matrix).
- **Symptom:** Spec/guide drift risk on a security-sensitive detail.
- **Evidence:** `docs/formats.md:114-115`; `rar_unrar.open_unrar_p`; E-38.
- **Today:** Implementation matches the guide.

### 8. HASHMAC / ConvertHashToMAC ahead of `format-rar` metadata requirement

- **Problem:** Guide names HASHMAC + UnRAR’s `ConvertHashToMAC`; code verifies
  natively and drops tweaked values from `member.hashes`; the cited Report RAR
  cost requirement does not mention HASHMAC.
- **Symptom:** Function-name wording suggests UnRAR performs the MAC step; archivey
  reimplements the transform. Ruling: keep “not exposed”; name → TM.
- **Evidence:** `docs/formats.md:117-119`; `rar_reader._member_hashes`;
  `tests/test_crypto_findings.py` F1; E-41.
- **Today:** Tweaked digests are not in `member.hashes`.

### 9. `compressed-streams` backend table omits lzip / zlib wrapper

- **Problem:** Home’s single-file list includes lzip and zlib; the
  `compressed-streams` “default backend” table has neither as named standalone
  codecs (raw Deflate yes; lzip only in later Decoder notes).
- **Symptom:** Spec↔guide completeness checks using only that table look like a
  Home error.
- **Evidence:** `docs/index.md:48-50`; `format-single-file-compressors` Purpose;
  `compressed-streams` Each supported codec has a default backend; E-69.
- **Today:** Library and single-file spec support the Home list.

### 10. Stored-digest / dedupe example length vs ruling

- **Problem:** Cluster ruling cuts the `content_key` sample and the
  stored-vs-computed closer; verification confirmed the sample runs.
- **Symptom:** Editorial only — no behavioral defect.
- **Evidence:** `docs/formats.md:195-220`; E-67 / E-68 executed; `documentation`
  Document the stored-digest matrix….
- **Today:** Recipe is correct; trim is a docs pass, not a library fix.
