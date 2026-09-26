# Tasks — cap the LZMA dictionary

## 1. The guard

- [x] 1.1 Move `check_decoder_memory` next to `StreamConfig` so `xz.py` and `lzip.py` can
      call it; it takes `DecoderLimits` directly.
- [x] 1.2 Raw LZMA/LZMA2 (7z, ZIP method 14), `.lzma` and lzip check the declared
      dictionary before building a decoder.
- [x] 1.3 xz passes the cap as liblzma's `memlimit`, with an overhead allowance, and maps
      the memlimit error to `ResourceLimitError`.
- [x] 1.4 Content probes and the inner-TAR probe decode with `DecoderLimits.UNLIMITED`.

## 2. Provenance

- [x] 2.1 Stamp `ResourceLimitError` on a probe-only single-file read as
      `format_unconfirmed`, with the diagnostic.
- [x] 2.2 `.lzma` over the cap opens as a stream whose reads refuse, so the refusal
      reaches the stamp instead of escaping `open_archive` before provenance is known.

## 3. Proof and documents

- [x] 3.1 `tests/test_decoder_limits.py`: each container from real writer output with its
      header rewritten, the default's boundary, the xz allowance, the seek path, and spies
      showing no decoder is built before the refusal.
- [x] 3.2 `DecoderLimits` docstring, CHANGELOG, `docs/extracting.md`, `IDEAS.md`.
- [x] 3.3 Archive this change.
