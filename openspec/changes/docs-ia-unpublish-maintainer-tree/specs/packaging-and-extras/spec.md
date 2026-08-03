## MODIFIED Requirements

### Requirement: Optional extras map only to libraries the code uses

User-facing extras SHALL list only libraries imported by `src/` at runtime for that
capability. A package used only by tests, decode oracles, fixture generation, or
fuzz harnesses MUST live in a PEP 735 dependency group (`dev`, `fuzz`, …) and be
absent from every user-facing extra.

The per-codec library choice and rationale SHALL be recorded in
`dev-docs/library-analysis.md`. A guard test or check script SHALL prevent dead or
test-only dependencies from returning to user-facing extras. A dependency pinned
ahead of its implementation phase, such as `tqdm` for the CLI, is permitted only
through an explicit documented allowlist in that guard.

#### Scenario: dependency-audit matrix

| Case | Expected |
| --- | --- |
| User-facing extra audited against `src/` imports | Every pinned package is reachable from runtime code or explicitly allowlisted |
| Library imported only by tests (`rarfile`, oracle `py7zr`, `ncompress`, fixture-only `pyzstd`) | Declared in `dev`; absent from runtime extras |
| `atheris` | Declared in `fuzz` group; absent from runtime extras and `[all]` |
| `pip install archivey[recommended]` on Python 3.11-3.13 | Installs `backports.zstd`; does not pull `zstandard` |
| `pip install archivey[recommended]` on Python 3.14+ | No third-party zstd package required; stdlib `compression.zstd` provides the backend |
| Extra lists a library no `src/` module imports and not allowlisted | Packaging audit fails |
