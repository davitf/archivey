# Tasks

## 1. Red: reproduce the silent fall-throughs

- [x] 1.1 `extract(src, dest, overwrite="skip")` replaces and deletes the local file the
      caller asked to keep, where `OverwritePolicy.SKIP` keeps it
- [x] 1.2 `extract(src, dest, on_error="stop")` continues past a CRC failure, where
      `OnError.STOP` raises
- [x] 1.3 Guards that must stay green: every member value still behaves as before, and a
      `StreamFormat` object handed to an `ArchiveFormat` parameter is still refused with
      the message naming its pairs

## 2. Green: one helper, every entry point

- [x] 2.1 `internal/enum_args.py`: `coerce_enum`, `coerce_enum_collection`,
      `normalize_spelling`; wrong-enum check ahead of the string branch, values winning
      over names on a tie
- [x] 2.2 `internal/format_args.py`: reject → coerce, with spellings read off
      `_FORMAT_NAMES` and `file_extension()` rather than a new table
- [x] 2.3 Call sites: `open_archive`, `open_stream`, `extract`, `extract_all`,
      `format_availability`, `detect_format(budget=)`, `ArchiveyConfig.__post_init__`
- [x] 2.4 Widen the annotations that now take a string, including the abstract
      `ArchiveReader.extract_all` so implementations stay compatible
- [x] 2.5 `cli/extract_cmd.py` drops `ExtractionPolicy(policy)` and its hand-rolled
      `.replace("-", "_")` in favour of the shared helper
- [x] 2.6 Verify red-green: with the coercion removed, 1.1 and 1.2 fail again

## 3. Tests

- [x] 3.1 `tests/test_enum_arguments.py`: red-green for both data-loss bugs, the
      per-enum collision guard, every member by every documented spelling, the
      wrong-enum type error, the bare-string refusal, and the ADR 0012 escape
- [x] 3.2 `tests/test_format_arguments.py`: the same collision guard for format
      spellings, plus positive coverage for `format="zip"` and `open_stream`'s
      resolution order; retire the three cases that asserted a string was refused

## 4. Docs and spec

- [x] 4.1 `docs/extracting.md` states the string spellings once, where the enums appear
- [x] 4.2 `error-handling`: the boundary rule and its matrix
- [x] 4.3 `backend-registry`: the `format=` contract, which said a string was refused

## 5. Verify

- [x] 5.1 `./scripts/check.sh` — `pyrefly` and `ty` both matter, the annotations moved
- [x] 5.2 `./scripts/test.sh --all-configs`
- [x] 5.3 `openspec validate --all --strict`
- [x] 5.4 `openspec archive coerce-public-enum-arguments --yes`
