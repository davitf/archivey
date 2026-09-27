## Why

RARLAB `unrar` is the only program that reads RAR member data, and on macOS it is the hard
install: Homebrew disabled the `rar` cask on 2026-09-01, so a pip user has to trust a
third-party tap or compile from source. `unar` (The Unarchiver's XADMaster) is free
software and `brew install unar` works. The 2026-09-01 decompressor matrix
(`dev-docs/investigations/alternative-rar-decompressors.md`) kept `unar` open: its stdout
output matches `unrar p` except on a known archive class, where it crashes or returns no
data with exit 0, and that class can be recognized from the native listing before `unar`
runs. The maintainer asked for `unar` as an alternative RAR backend, with the process
layer written so it can later serve other formats or codecs archivey does not decode.

## What Changes

- New `ArchiveyConfig.rar_decompressor` (`RarDecompressor.UNRAR`, the default, or
  `RarDecompressor.UNAR`). Selecting `unar` is explicit; archivey never changes program on
  its own, and a missing `unar` raises `PackageNotInstalledError`.
- New format-agnostic process layer `archivey.internal.external`: a banner-identifying
  finder with a probe timeout and a stat-keyed cache (the `unrar` finder's policy, written
  once), and `unar` argv, spawn and stdout ownership. Entries are named by decimal index,
  so no member name reaches argv and there is no include mask.
- New RAR policy `internal/backends/rar_unar.py`: refusals decided from the native parse
  before `unar` runs — encrypted data (password would go on argv; wrong password is exit 0),
  RAR5 solid members after an empty entry (crash or silent empty), RAR 1.5 compression
  (silent empty) — and the pipe layout of an all-entries run, which differs from `unrar p`.
- A prefixed single archive (SFX stub) is copied from the RAR's start for `unar`; a
  prefixed multi-volume set is refused.
- Glob-named members need no `rar_allow_glob_member_concatenation` under `unar`.

## Impact

- Specs: `format-rar` (data-program requirement), `packaging-and-extras` (single-tool
  requirement).
- Code: `config.py`, `__init__.py`, `internal/external/`, `rar_unar.py`, `rar_reader.py`
  (branches at the two spawn sites and the comment decode), `rar_unrar.py`
  (`decompress_rar3_blob` takes the spawn function).
- CI installs `unar` on Linux (apt) and macOS (Homebrew bottle).
