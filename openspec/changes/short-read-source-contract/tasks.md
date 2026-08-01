# Tasks — short-read-source-contract

The implementation landed in PR #219; these tasks record it against the spec deltas.

## 1. Source boundary

- [x] 1.1 Add `ensure_full_count_reads` to `streamtools/binaryio.py`; export it from the
      `streamtools` package root.
- [x] 1.2 Apply it to caller-supplied streams in `resolve_source` (per volume item, so
      `ConcatenatedFile` stays the resolved source the RAR/7z volume handling matches on)
      and in `open_stream`.
- [x] 1.3 Leave non-seekable sources untouched; confirm `PeekableStream` already
      coalesces so the `testing-contract` non-seekable rule is not weakened.
- [x] 1.4 Make `source_byte_size` look through a `BufferedReader` for its `size` /
      `try_get_size()` probes, so a nested `open_archive(reader.open(...))` keeps a cheap
      source size; leave the `SEEK_END` probe on the buffer.

## 2. Parser-level gathering

- [x] 2.1 `read_exact` for the RAR3 block header + body, the RAR5 preload + header body,
      and the `_HeaderDecryptStream` 16-byte AES-CBC block.
- [x] 2.2 `read_exact` for the ZIP local-header reads (`read_exact(fp, 30)`, the
      name/extra skip, the ZipCrypto header, the local name).
- [x] 2.3 `SlicingStream.read(n)` coalesces with `read_full_count` (ADR-0014
      stop-on-short); bounded `read(-1)` keeps `read_exact`; unbounded passes through.

## 3. Test coverage

- [x] 3.1 `ShortReadBytesIO` in `tests/streams_util.py`, capping `read` **and**
      `readinto` at `max_chunk` (default 1).
- [x] 3.2 `tests/test_short_read_sources.py`: parity sweep over every corpus format,
      every committed RAR/ZIP/7z fixture, and `open_stream` × `seekable`.
- [x] 3.3 Pin the RAR5 `fd.tell()`-derived offsets by driving `parse_rar_archive`
      directly from a short-returning source.
- [x] 3.4 `tests/test_slice.py`: full-count over a full-count inner, the deliberate
      stop-on-short boundary, and deliver-then-raise preservation over a decoder.
- [x] 3.5 Add the short-read case to the `tests/test_stream_inputs.py` matrix and a unit
      test for `ensure_full_count_reads`.

## 4. Verify

- [x] 4.1 `uv run ruff format --check` / `ruff check`, `uv run pyrefly check`,
      `uv run ty check`.
- [x] 4.2 Test suite green in all three dependency configurations (`[all]`,
      `[all-lowest]`, `[core-only]`).
- [x] 4.3 `openspec validate --strict short-read-source-contract`.
- [x] 4.4 Dry-run the archive on a scratch tree and diff `openspec/specs/` to confirm the
      `MODIFIED` header targets a requirement that actually exists (strict validation does
      not check this).
