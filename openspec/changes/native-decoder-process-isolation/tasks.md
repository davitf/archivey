# Tasks: native decoder process isolation

## 1. Config

- [ ] 1.1 `ProcessIsolation` enum and `ArchiveyConfig.process_isolation`, coerced from a
      name; `NONE` with `use_rapidgzip=ON` or `use_indexed_bzip2=ON` raises
      `ArchiveyUsageError`. Export from `archivey`.
- [ ] 1.2 Carry the value on `StreamConfig` and through detection.

## 2. Shared child protocol

- [ ] 2.1 `codecs/decoder_worker.py`: frame, error payload, exact reads, core dumps,
      SIGINT, the codec table with lazy imports, OPEN/CALL/GET/CLOSE.
- [ ] 2.2 `streams/decoder_child.py`: one channel per child, error rebuild, died and
      interrupted states, the pool (reuse rule, `atexit`, fork).
- [ ] 2.3 `codecs/isolated.py`: proxies and stand-in modules; `natives(config)`.
- [ ] 2.4 PPMd child on the generic worker; `ppmd_worker.py` removed.
- [ ] 2.5 rapidgzip worker and child on the shared framing and helpers.

## 3. Construction sites

- [ ] 3.1 Route every native decoder construction through the selector: deflate, gzip,
      zlib, bzip2, xz, lzip, LZMA Alone/raw/filters, zstd, lz4, brotli, Deflate64, PPMd,
      ISO zisofs, the 7z pipeline, detection probes.
- [ ] 3.2 `NONE`: PPMd in-process at any size, rapidgzip never selected.
- [ ] 3.3 `KNOWN_CRASHES`: every PPMd member in a child; a PPMd child is reused only after
      a clean end (no early stop, no quiesce NUL).

## 4. Tests

- [ ] 4.1 Every codec's fixtures decode byte-exact under `NATIVE_DECODERS`.
- [ ] 4.2 Crash blame, death classes, no reuse after an error,
      concurrency, start failure raises, PPMd reused only after a clean end.
- [ ] 4.3 Import guard: the worker imports nothing beyond the stdlib basics and one
      codec; the parent never imports pyppmd/inflate64/brotli/rapidgzip under
      `NATIVE_DECODERS`.
- [ ] 4.4 `NONE` with PPMd over the threshold and bit-3 pipe ZIP PPMd; rapidgzip `ON`
      refused. Default config: small PPMd member in a child, refused where none starts.
- [ ] 4.5 Leak oracle empties the pool before counting children.

## 5. Docs and verify

- [ ] 5.1 Security page: what each value isolates, what stays in-process, costs, the CLI
      subprocess recipe for a hard time limit.
- [ ] 5.2 `openspec validate native-decoder-process-isolation` (strict only warns on long requirements); `check.sh`;
      `test.sh --all-configs`.
