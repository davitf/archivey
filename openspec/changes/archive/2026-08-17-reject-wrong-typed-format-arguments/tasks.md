# Tasks

## 1. Red: reproduce all four failures as tests

- [x] 1.1 `format_availability(StreamFormat.ZSTD)` returns a fabricated record instead of
      raising — the record's `format` field violates the type this spec declares
- [x] 1.2 `open_archive(path, format=StreamFormat.ZSTD)` and
      `extract(path, dest, format=StreamFormat.ZSTD)` leak
      `AttributeError: 'StreamFormat' object has no attribute 'container'`
- [x] 1.3 `open_stream(src, format="zst")` discards the argument and auto-detects
- [x] 1.4 Guards that must stay green through the fix: `ArchiveFormat.UNKNOWN` is still a
      legitimate hintless NONE, `format=None` still auto-detects where it is allowed, and
      `open_stream` still takes both `StreamFormat` and a raw-stream `ArchiveFormat`

## 2. Green: one helper, four call sites

- [x] 2.1 `internal/format_args.py`: `check_archive_format` (with `allow_none` for the
      entry points that default to `None`) and `check_stream_or_archive_format`
- [x] 2.2 Message names what was passed and what was expected; for a `StreamFormat` it
      names the `ArchiveFormat` pairs containing that codec, derived from the predefined
      names in `types` rather than a second table that could drift
- [x] 2.3 Call it from `format_availability()` — on the public function, not the registry
      method, so internal callers holding an `ArchiveFormat` keep the plain lookup
- [x] 2.4 Call it from `open_archive()` and from `extract()` (in `extract` before the
      source is resolved and peeked, so the refusal costs no I/O)
- [x] 2.5 Call it from `open_stream()` before any I/O, closing the silent-ignore path
- [x] 2.6 Verify red-green: with the helper removed, the new tests fail again

## 3. Spec

- [x] 3.1 `backend-registry`: `format_availability()`'s contract states what a
      non-`ArchiveFormat` argument does, with a scenario row for the wrong-typed call
- [x] 3.2 `backend-registry`: the boundary rule the four entry points share, including
      why `open_stream` accepts the wider argument by design

## 4. Verify

- [x] 4.1 `./scripts/check.sh --fix` — `pyrefly` and `ty` both matter here
- [x] 4.2 `./scripts/test.sh` and `./scripts/test.sh --all-configs` (availability answers
      depend on which optional packages are installed, so this leg genuinely bites)
- [x] 4.3 `openspec validate --all --strict`
- [x] 4.4 Update `dev-docs/open-issues.md` P10 with what shipped, and close it
- [x] 4.5 `openspec archive 2026-08-17-reject-wrong-typed-format-arguments --yes`
