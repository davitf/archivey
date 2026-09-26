## 1. Implementation

- [x] 1.1 `RarDecompressor` and `ArchiveyConfig.rar_decompressor`, coerced from its name
- [x] 1.2 `internal/external/cli.py`: finder with banner probe, timeout, stat-keyed cache
- [x] 1.3 `internal/external/unar.py`: argv, spawn, `UnarOutputStream` exit mapping
- [x] 1.4 `rar_unar.py`: entry indexes, all-entries pipe layout, refusals, solid-pass selection
- [x] 1.5 `rar_reader.py`: nonsolid open, solid pass, comment decode, prefixed-source copy

## 2. Tests

- [x] 2.1 Every committed RAR fixture: `unar` matches `unrar` or refuses, open and stream
- [x] 2.2 Finder: lookalike, too old, hung probe, replaced binary
- [x] 2.3 argv shape; exit mapping (failure, crash, early close, digest-checked pipe)
- [x] 2.4 Default never spawns `unar`; selected-and-missing does not fall back

## 3. Docs and CI

- [x] 3.1 `docs/formats.md`, `docs/install.md`, `docs/migrating.md`, `docs/api.md`
- [x] 3.2 ADR 0002 amendment, threat-model C1, known issues, RAR handbook, IDEAS
- [x] 3.3 CI and `setup-dev-env.sh` install `unar`
