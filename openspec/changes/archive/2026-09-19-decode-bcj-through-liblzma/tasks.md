# Tasks

## 1. Decoder

- [x] 1.1 Replace `BcjDecoder`'s pybcj backend with liblzma: take an `lzma_filter_id`
      instead of a `decoder_attr`, run `[<branch filter>, FILTER_LZMA2]` in `FORMAT_RAW`,
      and frame fed bytes as LZMA2 uncompressed chunks (`_Lzma2Framer`)
- [x] 1.2 Stop passing the declared unpack size to the filter; keep it only for `finished`
- [x] 1.3 Emit the LZMA2 end marker from `flush()` so liblzma releases the branch filter's
      final look-ahead bytes
- [x] 1.4 Rename `BcjFilterStream`'s `decoder_attr` parameter to `lzma_filter_id`

## 2. Pipeline and registry

- [x] 2.1 `_BcjStage` carries `lzma_filter_id`; `_bcj_stage` reads it from the method entry
- [x] 2.2 Drop `_require_pybcj` and `_PYBCJ_REQUIREMENT` — BCJ needs no optional package
- [x] 2.3 Replace `SevenZipMethod.pybcj_attr` with `is_branch_filter`, and re-base
      `is_bcj()` on it

## 3. Packaging and docs

- [x] 3.1 Remove `pybcj` from the `[recommended]` extra and refresh the lockfile
- [x] 3.2 Drop the `pybcj` → `bcj` mapping from the extras guard test
- [x] 3.3 Update `docs/acknowledgements.md` and `dev-docs/library-analysis.md`
- [x] 3.4 Record both pybcj defects in `dev-docs/known-issues.md` with the measurements

## 4. Tests

- [x] 4.1 LZMA1+BCJ decodes with `bcj` unimportable (replaces the missing-pybcj hint test)
- [x] 4.2 Every branch filter round-trips a member whose length is not a whole number of
      blocks — IA64 is the case that failed before
- [x] 4.3 `BcjDecoder` accepts an `unpack_size` of 2 GiB and decodes the same bytes as a
      correctly-sized one, without a 2 GiB fixture
- [x] 4.4 Drop the now-meaningless `@requires("bcj")` markers from the BCJ round-trip tests

## 5. Finish

- [x] 5.1 `./scripts/check.sh` and `./scripts/test.sh`
- [x] 5.2 `openspec archive decode-bcj-through-liblzma --yes` and commit the specs diff
