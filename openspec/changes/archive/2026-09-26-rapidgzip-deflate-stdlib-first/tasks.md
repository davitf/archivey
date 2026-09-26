# Tasks — rapidgzip reads DEFLATE only after a stdlib proof

## 1. Codec

- [x] 1.1 `_StdlibUntilRandomAccess`: stdlib engine for reads and forward seeks; switch to
      rapidgzip at the first backward seek on input proven complete.
- [x] 1.2 `_ProofVerdictStream`: keep an `ArchiveyError` a failed proof pass saw.
- [x] 1.3 Wire gzip, zlib and deflate through it; a non-seekable source still goes
      straight to rapidgzip, which refuses it before decoding.
- [x] 1.4 Translate every rapidgzip `RuntimeError` (DEFLATE family and bzip2); leave a
      fault the caller's source raised untranslated.

## 2. Tests

- [x] 2.1 Truncated gzip / zlib / deflate, path and stream, four access patterns, in a
      child interpreter: `TruncatedError`, no abort.
- [x] 2.2 Canary: raw rapidgzip still aborts on the same inputs.
- [x] 2.3 Complete input: a backward seek opens rapidgzip once; a sequential read never
      opens it; a read to the end skips the separate pass.
- [x] 2.4 One-shot verdict and caller-source `RuntimeError` cases.

## 3. Docs

- [x] 3.1 `dev-docs/known-issues.md`, `docs/access-and-cost.md`, `docs/extracting.md`,
      `docs/formats.md`, `docs/gotchas.md`, `benchmarks/RESULTS.md`, `CHANGELOG.md`.

## 4. Archive

- [x] 4.1 `openspec archive rapidgzip-deflate-stdlib-first --yes`.
