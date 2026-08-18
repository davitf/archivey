# Session baseline for capability workers (coordinator)

Measured 2026-08-18 on this container before fan-out.

## Environment

- `scripts/setup-dev-env.sh` closing block: `ok unrar`, `ok 7z`, `ok benchmark toolchain`
- Dependency config for everyday work: **`[all]`** (`uv sync --group dev --extra all`)
- `./scripts/check.sh --fix` — all checks passed
- Python 3.11, archivey `0.2.0.dev0`

## `format_availability()` — this session

22 formats. **FULL=21, PARTIAL=0, NONE=1** (`UNKNOWN` only).

Seekable-required: DIRECTORY, ISO, RAR, SEVEN_Z, ZIP, UNKNOWN.
Forward-only: all compressors and TAR variants.

Do **not** inherit the earlier baseline's wording blindly — re-measure if you change
config. For `cfg` rows, name which config you checked (`[all]`, and `[core-only]` /
`[all-lowest]` when the claim depends on an optional package).

## Hard rules (from brief)

- Prefer **spec** over code when both exist (O-26).
- Verdicts: `verified` | `wrong` | `unverifiable (reason)`.
- `[TM]` → leave unverified (out of scope).
- `[code]` → **run** the block; do not eyeball.
- Co-cited pages that disagree → SPLIT / flag, never one verdict.
- Do **not** edit `docs/`, do **not** fix library defects, do **not** touch `src/`.
- Skip rows already carrying coordinator verdicts: **A-6, A-16, E-71**.
