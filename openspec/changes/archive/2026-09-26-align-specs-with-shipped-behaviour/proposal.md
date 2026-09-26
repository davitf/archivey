# Align four spec statements with what ships

## Why

Writing the user guide before 0.2.0 turned up four places where a main spec describes
behaviour the code does not have. In each case the code, the published docs and the
tests agree with each other, and only the spec is out of date.

| Spec | Says | Code does |
| --- | --- | --- |
| `backend-registry` | RAR without `unrar` on `PATH` is PARTIAL | `format_availability(RAR)` is FULL whatever is on `PATH`; `docs/install.md` says so |
| `backend-registry` | ZIP SHALL stay PARTIAL "until Phase 6", even with every codec installed | ZIP is FULL with Deflate64, Zstd and PPMd installed (`_CONTAINER_OPTIONAL_CODECS` in `internal/registry.py`) |
| `safe-extraction` | `STANDARD` strips setuid/setgid | `STANDARD` strips setuid, setgid and sticky (`_HIGH_BITS = 0o7000` in `internal/filters.py`); `docs/extracting.md` says so |
| `testing-contract` | `py7zr` and the `7z` CLI are 7z oracles | `tests/test_sevenzip_oracle.py` compares against `py7zr` only; the `7z` CLI only builds fixtures |

## What changes

The specs change to describe the shipped behaviour. No code, test or published doc
changes: all three already match the code.

## Impact

- `openspec/specs/backend-registry/spec.md` — two requirements, one scenario row.
- `openspec/specs/safe-extraction/spec.md` — one matrix cell, one scenario row.
- `openspec/specs/testing-contract/spec.md` — one requirement sentence, one scenario row.
- Not breaking.
