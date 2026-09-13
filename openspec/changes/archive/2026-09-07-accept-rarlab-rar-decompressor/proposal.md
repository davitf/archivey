## Why

Ubuntu/Debian `apt install rar` puts RARLAB's trialware writer on `PATH` and only
Suggests `unrar`. Archivey refused that binary because the identification banner is
`RAR 7.00 … Alexander Roshal`, not `UNRAR 7.00`. On 7.00, `rar p` matched `unrar p`
on the argv archivey actually spawns (18 cases). Handbook §10 #20.

## What Changes

- `find_rarlab_unrar` looks up `unrar` first, then `rar`. The first RARLAB banner at
  version 6.0+ wins. A lookalike or too-old `unrar` does not hide a usable `rar`.
- Banner sniff accepts standalone `RAR x.yy` as well as `UNRAR x.yy`. `RAR` does not
  match inside `UNRAR`. Vendor still requires `Alexander Roshal` or `RARLAB`.
- Errors name both acceptable binaries. `unar` / `7z` / `unrar-free` stay refused.
- Spawn remains `<binary> p` only — never `x` / extract-to-disk.

## Capabilities

### New Capabilities

### Modified Capabilities

- `format-rar` — member data may use RARLAB `rar` when `unrar` is missing or unusable
- `packaging-and-extras` — the sole supported decompressor family is RARLAB `unrar` or
  `rar`, not a fallback matrix to other tools

## Decisions

- Same 6.0 floor for both binaries. Writer `p` was measured on 7.00; 6.x apt `unrar`
  is the documented floor and applying it to `rar` keeps one rule.
- Prefer `unrar` when both are usable. The freeware reader is the intended install.
- Fall through from a lookalike or too-old `unrar` to `rar`. "Prefer unrar" does not
  mean "refuse rar because a broken unrar exists".
- Windows `Rar.exe` banner is unmeasured; lookup still tries `rar` via `PATHEXT`.

## Impact

- `src/archivey/internal/backends/rar_unrar.py`, `tests/test_rar_reader.py`
- `docs/install.md` (and the pages that point at that section)
- `dev-docs/formats/rar.md` §1, §3, §10 #20
