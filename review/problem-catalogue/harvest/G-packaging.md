# Harvest G — Packaging and platform

Bounded drop from Worker G verification (Topic 8 pass 1). Neutral phrasing; no
investigation beyond what the claim rows already touched.

---

### 1. `[free-threaded]` docs omit the `<3.14` gate on `backports.zstd`

- **Problem:** Prose lists `backports.zstd` as always in `[free-threaded]`, with a
  version caveat only for `cryptography`.
- **Symptom:** On 3.14+ the extra does not pull `backports.zstd` (stdlib
  `compression.zstd`); an “exactly these packages” reading is wrong.
- **Evidence:** `docs/acknowledgements.md:67`; `packaging-and-extras` extras matrix;
  `pyproject.toml` `free-threaded` markers; G-9.
- **Today:** Spec/`pyproject` use `backports.zstd; python_version < '3.14'` and
  `cryptography; python_version >= '3.14'`.

### 2. Part 1 zstd “missing package” probe was a name trap

- **Problem:** Cluster note said ZST was FULL while neither `zstandard` nor
  `backports.zstd` appeared in metadata.
- **Symptom:** Looks like a docs/packaging mismatch; it is not.
- **Evidence:** This session `[all]` / 3.11: metadata Name `backports.zstd` 1.6.0,
  import `backports.zstd`, wheel dir `backports_zstd-*.dist-info`; G-6 verified.
- **Today:** Prefer metadata Name / import path, not only the on-disk dist-info
  directory spelling.

### 3. `install.md` free-threaded section duplicates the lead paragraph

- **Problem:** Lines 30–34 near-verbatim repeat 15–18.
- **Symptom:** Two homes for the same install line (scope → page fold).
- **Evidence:** `docs/install.md:15-18` vs `:30-34`; G-8 ruling.

### 4. `install.md` still missing the two §B deliverables

- **Problem:** “What each format needs” remains a pointer; Q4 re-index and
  `format_availability()` query are unwritten.
- **Symptom:** Install page does not answer “what does this extra unlock?” or
  “what does this install actually support?” at runtime.
- **Evidence:** `docs/install.md:23-28`; scope Q4; G-24, G-25, G-25a (wrong-typed
  `format=` must be one sentence when G-25 is written).

### 5. Install lead groups RAR with pip extras

- **Problem:** Opening sentence lists RAR beside ISO / extended 7z codecs / seek
  as “an opt-in extra.”
- **Symptom:** Reader may think `pip install` somehow supplies RAR, or that RAR
  listing needs an extra.
- **Evidence:** `docs/install.md:3-6`; packaging-and-extras core (native RAR
  metadata); E-37 / `unrar` for member data.
- **Today:** RAR metadata is core; member data needs RARLAB `unrar` on `PATH`.

### 6. CI all-lowest comment still names `zstandard`

- **Problem:** Workflow rationale for lowest-direct still gives `zstandard 0.23`
  as an example floor pin.
- **Symptom:** Comment drift vs current `[recommended]` / `backports.zstd`.
- **Evidence:** `.github/workflows/ci.yml` all-lowest matrix comment; G-12
  (behaviour claim still true).

### 7. Progress bars also stay off when stderr is not a TTY

- **Problem:** Guide says progress needs `tqdm` / still runs without it; does not
  mention the non-TTY display gate.
- **Symptom:** CI/pipes with tqdm installed still show no bar.
- **Evidence:** `docs/cli.md:3-4`; `src/archivey/cli/progress.py` `_display_stream`;
  G-23.
- **Today:** Missing tqdm **or** non-interactive display → no callback.

### 8. Acknowledgements free-threaded row vs support-matrix table framing

- **Problem:** Acknowledgements states a universal package list; the matrix is
  explicitly “Measured on CPython 3.13.7t.”
- **Symptom:** Easy to read the table’s `backports.zstd` Yes as 3.14t truth too.
- **Evidence:** `docs/acknowledgements.md:67` vs `docs/support-matrix.md:64-78`;
  G-9 / G-16.
