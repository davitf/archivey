# Verdicts — Worker G (Packaging and platform)

Config for this pass: **`[all]`** (`uv sync --group dev --extra all`), unless a row notes
otherwise. Spec citations preferred over `src/` / `pyproject.toml` when both speak (O-26).
`[code]` rows were checked with `uv run --no-sync`. Free-threaded install/GIL claims settle
against `packaging-and-extras` + `.github/workflows/ci.yml`; **3.13t was not installed in
this container** (downloadable via `uv` but not run).

| # | V | Evidence |
|---|---|---|
| G-1 | verified | `packaging-and-extras` Zero-dependency core: bare install, no third-party runtime deps; ZIP/TAR/stdlib codecs/directory. Spot-check: `project.dependencies` absent; `format_availability` FULL for ZIP/TAR/GZ/BZ2/XZ/Z/DIRECTORY on `[all]`. |
| G-2 | verified | Spec Optional extras: exactly four user-facing extras; SHALL NOT ship per-format extras; member codecs shared. `pyproject.toml` keys: `recommended`, `seekable`, `free-threaded`, `all`. Matches `install.md:15-16` / acknowledgements / formats codec note. |
| G-3 | verified | `[code]` `install.md:8-13` four lines match extras + descriptions: bare = zero-dep core ZIP/TAR/gz/bz2/xz/directory; `[recommended]` = “every format and codec that installs everywhere” (spec Enables); `[seekable]` = rapidgzip gz/bz2 random access and speed; `[all]` = both. Confirmed against `pyproject.toml` optional-dependencies + packaging-and-extras extras matrix. (`[free-threaded]` is the fourth extra but lives in prose, not this block — intentional.) |
| G-4 | verified | Spec `[recommended]` Enables: “Every format and codec that installs everywhere”. Same wording on `install.md:10` / formats recommended install. |
| G-5 | verified | Spec `[seekable]` → `rapidgzip`; Enables faster gzip/bzip2 + random access. `seekable-decompressor-streams` Gzip and bzip2 random access use rapidgzip only. Matches install/acknowledgements/formats. **cfg `[all]`**: `rapidgzip` present. |
| G-6 | verified | Spec + `pyproject.toml` `[recommended]`: `pyppmd`, `inflate64`, `brotli`, `lz4`, `pybcj`, `backports.zstd` (`python_version < '3.14'`), `cryptography`, `pycdlib`, `tqdm`. **cfg `[all]` on 3.11**: `importlib.metadata` reports `backports.zstd` 1.6.0; `codecs._zstd` is that module; `ZST`/`TAR_ZST` FULL with empty `missing`. Part 1’s “neither package” note does **not** reproduce here (wheel dir is `backports_zstd-*.dist-info`; metadata Name / import is `backports.zstd`). |
| G-7 | verified | Spec SHALL keep `[all]` as `[recommended]` + `[seekable]`; `all = ["archivey[recommended,seekable]"]`. Matches install/acknowledgements. |
| G-8 | verified | Spec `[free-threaded]` = measured subset that leaves GIL disabled. Claim text matches `install.md:16-18` and `:30-34` (near-verbatim fold — ruling → page, not a false claim). Also `support-matrix.md:67-68` / acknowledgements. Live 3.13t not run. |
| G-9 | wrong | Spec / `pyproject.toml`: `backports.zstd` only when `python_version < '3.14'`, `cryptography` only when `>= '3.14'`. Acknowledgements + claim say “exactly … `backports.zstd`, and `cryptography` on 3.14+ only” — treats `backports.zstd` as unconditional. On 3.14+ `[free-threaded]` is `pycdlib`/`lz4`/`tqdm`/`cryptography` (stdlib zstd), not `backports.zstd`. Table at `support-matrix.md:70-78` is fine for its 3.13.7t measurement (G-16). |
| G-10 | verified | Spec Supported runtime: Python 3.11+; `requires-python = ">=3.11"`. Spot-check: no `.so`/`.pyd` under installed `archivey` package (pure Python). |
| G-11 | verified | `ci.yml` `test` matrix include + `free-threaded-concurrency` job match the six guide rows (Linux 3.11–3.14 all; Linux 3.11+3.14 core-only; Linux 3.11 all-lowest; Linux 3.13t; macOS 3.11+3.14; Windows 3.11+3.14). |
| G-12 | verified | CI `all-lowest` uses `--resolution lowest-direct`; CONTRIBUTING “Before pushing…” same rationale (presence *and* version). Matches guide. |
| G-13 | verified | Explicit non-claim. Spec declares support for Linux/macOS/Windows 3.11+ only — consistent with “other platforms expected, not tested.” |
| G-14 | verified | Explicit non-claim. Spec / classifiers are CPython-oriented; optional backends are C/C++ (pyproject/CI free-threaded comments). Core pure-Python matches G-10. |
| G-15 | verified | Spec (seekable out of recommended; free-threaded measured set) + CI comments: undeclared free-thread support → import re-enables GIL. Guide statement matches. Live 3.13t import not run here. |
| G-16 | verified | Seven-row table matches spec `[free-threaded]` membership + CI exclusion list (`pyppmd`/`inflate64`/`brotli`/`rapidgzip` re-enable GIL; `cryptography` 3.14+ only). Measured-on-3.13.7t framing matches CI. |
| G-17 | verified | Spec extras matrix: `[recommended]` on free-threaded 3.13 **Fails** — cryptography→cffi; installs on 3.14t. Matches guide. Live `pip` on 3.13t not run (prefer spec). |
| G-18 | verified | Spec SHALL treat `[free-threaded]` as measured, moving; MAY collapse into `[recommended]`; not a claim about archivey’s own code only. |
| G-19 | verified | `ci.yml` “Assert the GIL is still disabled with those extras” (`sys._is_gil_enabled()` assert after `--extra free-threaded`). Matches guide. |
| G-20 | verified | Same job: full `pytest tests/` core-only, then GIL-safe extras + full suite again. Comments: whole suite, not only concurrency markers. Matches `support-matrix.md:56-58`. |
| G-21 | verified | Four non-claims on `support-matrix.md:98-108` match CI Linux-only FT job, excluded “No” packages, and `reader-concurrency` post-materialization member-stream seam (not iteration/extract/`stream_members`/`close`; not a speedup promise). packaging-and-extras: not a parallel-speed guarantee. |
| G-22 | verified | Spec archivey console entry points ship with base package; `project.scripts.archivey`. Spot-check: `uv run --no-sync archivey --version` → `archivey 0.2.0.dev0`. |
| G-23 | verified | Spec + `cli`: tqdm from `[recommended]`; absence suppresses progress, command remains. **cfg `[all]`**: `tqdm` installed; CLI runs. Spot-check: `make_progress_callback` returns `None` on `ImportError` for tqdm (and also when display is non-TTY). |
| G-24 | verified | Silence / Guide gap true: `install.md:23-28` is still a pointer; maintainer Q4 restores four-row extra→formats re-index (~12 lines); `formats.md` stays authoritative. No prose yet. |
| G-25 | verified | Silence / Guide gap true: same section should document `format_availability()` FULL/PARTIAL/NONE + `missing` (must-explain #15). API exists (`registry.py` / public `format_availability`). Unwritten. |
| G-25a | verified | `backend-registry` wrong-typed format → `ArchiveyUsageError`; `format_args.py`. Spot-check: `format_availability(StreamFormat.ZSTD)` and `open_archive(..., format=StreamFormat.ZSTD)` raise `ArchiveyUsageError`. Must be one sentence inside G-25 when written. |
| G-26 | verified | Core uses stdlib `zipfile`/`tarfile`/`gzip`/`bz2`/`lzma` (+ zlib Deflate path; `compression.zstd` on 3.14+). Matches acknowledgements list + packaging-and-extras core / `compressed-streams` default backends. |
| G-27 | verified | PEP 735 groups `dev` / `docs` / `fuzz` in `pyproject.toml`; each listed acknowledgements package’s stated use matches (oracles, ncompress fixtures, optional-backend exercise, urllib3/fsspec streams, hypothesis, pytest stack, ruff/pyrefly/ty/pre-commit, mkdocs…, atheris fuzz). Ellipsis on docs row is honest (`mkdocs-autorefs` / `griffe-fieldz` also present). |

## Notes for coordinator

### Wrong rows
- **G-9** — `[free-threaded]` “exactly … `backports.zstd`” omits the `<3.14` marker that spec/`pyproject.toml` apply (parallel to cryptography’s `>=3.14` marker)

### Config notes (`cfg`)
- Everyday verification: **`[all]`** on CPython **3.11.16**.
- Session `format_availability`: 22 formats, FULL=21, PARTIAL=0, NONE=1 (`UNKNOWN`) — reconfirmed.
- G-6: `backports.zstd` **is** installed under that distribution name; Part 1 disagreement resolved as probe/name confusion, not a packaging bug.
- G-23: checked with tqdm present; absence path via `ImportError` branch in `cli/progress.py`.
- Free-threaded empirical install/GIL: **not run** (no 3.13t in the venv); settled from spec + `ci.yml`.

### Cross-cluster / process
- G-8 fold (`install.md:30-34` ↔ `:15-18`) is editorial → page, claim still true.
- G-24 / G-25 / G-25a are planned Guide prose (scope Q4 + P10 typing); silence verified, not library defects.
- Stale CI comment still says `zstandard 0.23` in the all-lowest rationale — harvest only.
- G-1’s adjacent install sentence groups “RAR” with pip extras; RAR metadata is core and data needs `unrar` (E-37 territory) — soft harvest, not a G-1 fail.

### Counts
- **verified:** 27
- **wrong:** 1 (G-9)
- **unverifiable:** 0
- **rows:** 28 (G-1…G-27 including G-25a)
