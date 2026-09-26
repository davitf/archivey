# Align spec statements with what ships

## Why

Writing the user guide before 0.2.0 turned up places where a main spec describes
behaviour the code does not have. In each case the code, the published docs and the
tests agree with each other, and only the spec is out of date.

| Spec | Says | Code does |
| --- | --- | --- |
| `backend-registry` | RAR without `unrar` on `PATH` is PARTIAL | `format_availability(RAR)` is FULL whatever is on `PATH`; `docs/install.md` says so |
| `backend-registry` | ZIP SHALL stay PARTIAL "until Phase 6", even with every codec installed | ZIP is FULL with Deflate64, Zstd and PPMd installed (`_CONTAINER_OPTIONAL_CODECS` in `internal/registry.py`) |
| `safe-extraction` | `STANDARD` strips setuid/setgid | `STANDARD` strips setuid, setgid and sticky (`_HIGH_BITS = 0o7000` in `internal/filters.py`); `docs/extracting.md` says so |
| `testing-contract` | `py7zr` and the `7z` CLI are 7z oracles | `tests/test_sevenzip_oracle.py` compares against `py7zr` only; the `7z` CLI only builds fixtures |
| `safe-extraction` | `STANDARD` strips uid/gid | `transform_standard` keeps uid/gid on the member a `filter` sees; only the `chown` is TRUSTED-only (`_apply_metadata` in `internal/extraction.py`) |
| `format-detection`, `detection-cost` | `FormatInfo` has five fields; `TierSkip` is not specified | `FormatInfo` also carries `cost_receipt` and `unavailable_tiers`, which `docs/api.md` documents with `DetectionCostReceipt` and `TierSkip` |

## What changes

The specs change to describe the shipped behaviour. No code or test changes.

`FormatInfo.cost_receipt` and `FormatInfo.unavailable_tiers` become part of the
`detect_format` contract, because the API page already documents the receipt and
`TierSkip`, and `unavailable_tiers` is the only place a caller gets a `TierSkip`.
`FormatInfo.corroborated` stays outside the contract, as `probe-provenance-unconfirmed`
decided: its `False` cannot tell an uncorroborated probe from a result that was not a
probe.

## Impact

- `openspec/specs/backend-registry/spec.md` — two requirements, one scenario row.
- `openspec/specs/safe-extraction/spec.md` — one matrix cell, the uid/gid row split in two,
  two scenario rows.
- `openspec/specs/format-detection/spec.md` — two `FormatInfo` fields and the
  `corroborated` exclusion.
- `openspec/specs/detection-cost/spec.md` — one added requirement for the receipt and
  the skipped tiers on `FormatInfo`.
- `openspec/specs/testing-contract/spec.md` — one requirement sentence, one scenario row.
- Not breaking.
