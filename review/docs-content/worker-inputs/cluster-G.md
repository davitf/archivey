# G. Packaging and platform

Spec: `packaging-and-extras`.
Pages: `install`, `support-matrix`, `acknowledgements`, `migrating`, `index`, `formats`.

| # | Claim | Stated at | Settles it | Ruling | V |
|---|---|---|---|---|---|
| G-1 | **The core installs with no dependencies at all** and reads ZIP, TAR, directories and the stdlib codecs | `install.md:3-4`, `index.md:52-53`, `formats.md:8-14`, `acknowledgements.md:61` | `packaging-and-extras:23` | Keep | |
| G-2 | **Exactly four extras exist and there are no per-format ones** — member codecs are shared across containers, so a format name would be the wrong thing to install | `install.md:15-16`, `acknowledgements.md:70-72`, `formats.md:35-36` | `packaging-and-extras:50` | Keep | |
| G-3 | `[code]` the four `pip install` lines are the correct four, with the correct one-line descriptions (`archivey`, `[recommended]`, `[seekable]`, `[all]`) | `install.md:8-13` | `packaging-and-extras:50`, `pyproject.toml` | Keep — the page's deliverable | |
| G-4 | **`[recommended]` is "every format and codec that installs everywhere"** | `install.md:10`, `formats.md:25-26` | `packaging-and-extras:50` | Keep | |
| G-5 | **`[seekable]` is rapidgzip**, giving gz/bz2 random access and speed | `install.md:11`, `acknowledgements.md:66`, `formats.md:25-26` | `packaging-and-extras:50`, `seekable-decompressor-streams:69` | Keep | |
| G-6 | `[recommended]` pulls exactly: `pyppmd`, `inflate64`, `brotli`, `lz4`, `pybcj`, `backports.zstd` (before 3.14; 3.14+ uses stdlib `compression.zstd`), `cryptography`, `pycdlib`, `tqdm` | `acknowledgements.md:65`, `formats.md:18` | `packaging-and-extras:50`, `packaging-and-extras:157`, `pyproject.toml` | Keep · `cfg` — see the Part 1 note: this session has zstd working with **neither** package installed under those names | |
| G-7 | `[all]` is `[recommended]` + `[seekable]` | `install.md:12`, `acknowledgements.md:68` | `packaging-and-extras:50` | Keep | |
| G-8 | On a free-threaded build use **`archivey[free-threaded]`** — the measured subset of extras that leaves the GIL disabled | `install.md:16-18`, `install.md:30-34`, `support-matrix.md:67-68`, `acknowledgements.md:67` | `packaging-and-extras:50`, `packaging-and-extras:197` | `install.md:30-34` = **`→ page` (fold)**, near-verbatim repeat of `15-18` | |
| G-9 | **`archivey[free-threaded]` is exactly** `pycdlib`, `lz4`, `tqdm`, `backports.zstd`, and `cryptography` on 3.14+ only | `acknowledgements.md:67`, `support-matrix.md:70-78` | `packaging-and-extras:50`, `pyproject.toml` | Keep | |
| G-10 | **`archivey` requires Python 3.11+** and is pure Python with no compiled extensions of its own | `support-matrix.md:9-10`, `acknowledgements.md:76-82` | `packaging-and-extras:197`, `pyproject.toml` | Keep | |
| G-11 | The CI matrix is exactly the six listed legs (Linux 3.11–3.14 all extras; Linux 3.11+3.14 core; Linux 3.11 all-lowest; Linux 3.13t; macOS 3.11+3.14; Windows 3.11+3.14) | `support-matrix.md:12-20` | `.github/workflows/ci.yml`, `packaging-and-extras:197` | Keep | |
| G-12 | **The minimum-versions leg tests the floor of each declared range**, because optional libraries change behaviour by version as well as presence | `support-matrix.md:21-24` | `.github/workflows/ci.yml`, `CONTRIBUTING.md` §"Before pushing…" | Keep | |
| G-13 | **Other platforms (BSDs, other CPython builds) are expected to work and are not tested** — an explicit non-claim | `support-matrix.md:25-27` | `packaging-and-extras:197` | Keep | |
| G-14 | **Non-CPython interpreters are not tested**; the core is pure Python but the accelerators and codec backends are C/C++ | `support-matrix.md:29-33` | `packaging-and-extras:197` | Keep | |
| G-15 | **An undeclared C extension makes CPython silently re-enable the GIL** on a free-threaded build | `support-matrix.md:62-64` | `packaging-and-extras:197` | Keep — the fact the whole section exists for | |
| G-16 | The free-threading package table is correct in all seven rows (pycdlib / backports.zstd / lz4 / tqdm yes; `cryptography` 3.14+ only; `rapidgzip` no; `pyppmd`+`inflate64`+`brotli` no) | `support-matrix.md:70-78` | `packaging-and-extras:50`, `.github/workflows/ci.yml` | Keep — the actionable core | |
| G-17 | **`pip install archivey[recommended]` fails on free-threaded 3.13** because `cryptography`'s `cffi` dependency rejects it outright; it installs on 3.14t | `support-matrix.md:76`, `support-matrix.md:86-88` | `packaging-and-extras:50` | Keep | |
| G-18 | **`[free-threaded]` is a moving set**, not a guarantee about archivey's own code, and may eventually stop being a separate extra | `support-matrix.md:89-91` | `packaging-and-extras:50` | Keep | |
| G-19 | **The CI job asserts the GIL is still disabled after installing `[free-threaded]`**, so a package regression fails the job rather than quietly testing a GIL-ed interpreter | `support-matrix.md:93-96` | `.github/workflows/ci.yml` | Keep | |
| G-20 | The free-threading claim is verified by a **required CI job on Linux CPython 3.13t running the whole test suite** in two stages (zero-dep core, then core + GIL-safe extras) | `support-matrix.md:56-58` | `.github/workflows/ci.yml` | Keep | |
| G-21 | Four explicit **non-claims**: macOS/Windows free-threaded builds, the "No"-row packages, everything except member streams, and parallel **speedup** | `support-matrix.md:98-108` | `reader-concurrency:22`, `.github/workflows/ci.yml` | Keep — what an explicit non-coverage list looks like when done well | |
| G-22 | **`archivey`'s console entry point ships with the base package** | `cli.md:3`, `install.md:8-9` | `packaging-and-extras:262` | Keep | |
| G-23 | **Progress bars need `tqdm`, which comes with `[recommended]`; without it the command still runs** | `cli.md:3-4`, `acknowledgements.md:65` | `packaging-and-extras:50`, `cli:16` | Keep · `cfg` | |
| G-24 | **§B row 2's second half, unwritten:** a four-row **extra → formats re-index** (core / `[recommended]` / `[seekable]` / `[free-threaded]`), naming which formats each unlocks, with `formats.md` still authoritative | `install.md:23-28` is the section that receives it | `packaging-and-extras:50` | **Guide, ~12 lines** — restored by maintainer decision (`scope.md` Q4), bounded to a re-index | |
| G-25 | **§B row 2's first half, unwritten:** `format_availability()` as a runtime query — FULL / PARTIAL / NONE and what `missing` gives you (must-explain #15) | `install.md:23-28` receives it | `src/archivey/internal/registry.py:58-90`, `:314` | **Guide, ~10 lines** | |
| G-25a | **When G-25 is written, the query takes an `ArchiveFormat` and nothing else.** A `StreamFormat` (or any other type) raises `ArchiveyUsageError` rather than returning a record — as does a wrong-typed `format=` on `open_archive` / `extract` / `open_stream`. Shipped after this baseline was taken (P10, `2026-08-17-reject-wrong-typed-format-arguments`), so the section must not be written from the old behaviour | `install.md:23-28` receives it | `src/archivey/internal/format_args.py`; `backend-registry` §"Format support is tri-state and compositional" and §"A format argument outside its declared type is a usage error" (named rather than numbered — this PR's own inserts move the line) | **Guide** — one sentence inside G-25's ~10 lines | |
| G-26 | The stdlib modules archivey always uses are `zipfile`, `tarfile`, `gzip`, `bz2`, `lzma`, `zlib`, and on 3.14+ `compression.zstd` | `acknowledgements.md:76-82` | `packaging-and-extras:23`, `compressed-streams:72` | Keep | |
| G-27 | The **dev/test dependency table** is accurate: the PEP 735 `dev` / `docs` / `fuzz` groups, and each listed package's stated use | `acknowledgements.md:84-98` | `pyproject.toml`, `packaging-and-extras:181` | Keep | |

## G — problems and gaps met while extracting

- **G-6 is the one row where the baseline already disagrees with the page.** Python 3.11
  in this session reports `ZST` as `FULL` with an empty `missing`, while neither
  `zstandard` nor a package importable as `backports.zstd` shows in
  `importlib.metadata`. Either the page's "`[recommended]` → `backports.zstd`" row names
  the wrong thing, or the probe used in Part 1 is looking under the wrong distribution
  name. **Not resolved here** — it is a claim row, and it is exactly the kind of
  spec/design discrepancy the brief says to pause on rather than settle silently.
- `install.md` is 34 lines and carries **two unwritten §B rows** (G-24, G-25) that
  roughly double it. It is also the page most exposed to `[core-only]`: every line of it
  is a dependency claim.

---

