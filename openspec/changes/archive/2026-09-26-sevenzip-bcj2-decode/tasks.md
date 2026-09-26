## 0. Gate

- [x] 0.1 Before archiving, check all seven MODIFIED blocks against the live
      requirements: `format-7z` "Decode folder coder chains through compressed-streams",
      "Reject unsupported codecs without fallback" and "Decode BCJ branch filters through
      liblzma"; `packaging-and-extras` "Optional extras enable specific capabilities";
      `error-handling` "Single rooted archive exception hierarchy"; `backend-registry`
      "Format support is tri-state and compositional"; `testing-contract` "Cross-validate
      native readers against reference oracles". A MODIFIED delta replaces the whole
      block, so anything the live requirement gained after this change was written would
      be deleted silently. Dry-run `openspec archive` on a scratch copy of `openspec/`,
      `diff -u` the result against the live specs, and confirm every removed line is one
      this change means to replace.
- [x] 0.2 (Answered with libarchive's older-7-Zip fixtures; see design.md "Implementation notes".) Answer design open question 1 if an older 7-Zip is available (does its output
      leave `rc` bytes or a non-zero final `code`?). If not, keep D5 as written.

## 1. Red tests first

- [x] 1.1 Fixture helper: build BCJ2 archives with the `7z` CLI (`chmod +x` the input
      before `-mx9`, or the forced `-m0=BCJ2 …` form), skipping when the CLI is absent.
      Cover `-mx9` single, `-mx9` solid, `-ms=off`, encrypted with `-mhe=on`, and forced
      BCJ2 on inputs ending in `E8` and in `0F`.
- [x] 1.2 Failing tests: each fixture reads byte-for-byte equal to its input, through
      `open()`, `stream_members()` and `extract_all()`.
- [x] 1.3 Failing test: `stream_members()` over the solid fixture opens the folder's pack
      streams once (count views or seeks on the source).
- [x] 1.4 Failing tests on `Bcj2DecoderStream` directly: each input truncated by one byte
      raises `TruncatedError`; one extra byte in `main`, `call` or `jump` raises
      `CorruptionError`; input block sizes 1, 7 and 64 KiB give the same output.
- [x] 1.5 Failing tests: a folder whose coder output is bound twice, or whose bind pairs
      form a cycle, raises `CorruptionError` with no output bytes; the same folder
      encrypted raises `CorruptionError` on the first password attempt, not
      `EncryptionError`; a folder with a multi-input coder other than BCJ2 raises
      `UnsupportedFeatureError`.
- [x] 1.5a Failing test: a forced BCJ2 folder whose `main` branch is `[7zAES, BZip2]` (or
      `-m1=BZip2` unencrypted) decodes; its BZip2 stage gets the branch predecessor's
      size (D1 step 3).
- [x] 1.5b Failing test: `member.compression` for the `-mx9` fixture is exactly
      `(BCJ2, LZMA2)` (D9).
- [x] 1.5c Failing test: with `main` followed by a large extra tail, the end-of-output
      check raises `CorruptionError` after one extra byte is read from it (count reads
      on a wrapper), not after the tail is drained (D5).
- [x] 1.6 Replace `test_bcj2_folder_is_rejected` with a test that a multi-input coder
      other than BCJ2 is rejected. Keep
      `test_bcj2_nonsolid_pack_streams_are_not_member_scaled`.

## 2. Implement

- [x] 2.1 Move `prototype/bcj2.py` to `src/archivey/internal/streams/bcj2.py`, with its
      one-byte end-of-output check (`_check_inputs_finished`, D5). Drop `leftover()`,
      which drains and exists only for the verification script. Classify the new stream in the
      four `tests/test_stream_bases.py` inventories.
- [x] 2.2 `plan_folder`: resolve the tree (D1), planning each branch from its own coder
      indices so a `SINGLE` stage's input length is the branch predecessor's, not
      `unpack_sizes[index - 1]`. Linear folders keep their current plan exactly; add a
      test that pins that, over the existing fixtures. Keep planning outside the
      password check's error mapping.
- [x] 2.3 `open_folder_pipeline`: take one view per pack stream, run each branch's stages,
      and put `Bcj2DecoderStream` over the four branch outputs. The BCJ2 stage owns and
      closes its branches.
- [x] 2.4 `sevenzip_reader`: `_folder_pack_views` in place of `_folder_pack_view`, used by
      `_open_folder_stream` and by the password check.
- [x] 2.5 Leave `decode_encoded_header` linear-only (D7), with a test.
- [x] 2.6 `_build_folder_compression`: for a tree, list the root and its `main` branch
      only (D9). Builds on the separate fix that puts 7z tuples in the pack direction;
      if that has not landed, land it first.

## 3. Docs and records

- [x] 3.1 `dev-docs/formats/7z.md`: the at-a-glance "Refuses" row, §1's BCJ2 paragraph,
      §3's `-mx9` table, the two §5 rows (the refusal and its message), the §6 decision
      row, §8's verify table, plus a §5 row for the speed cost (D3).
- [x] 3.2 Drop "BCJ2 unsupported" from the same list as the proposal's: `docs/formats.md`,
      `AGENTS.md`, `openspec/project.md`, `dev-docs/PLAN.md`, `dev-docs/open-issues.md`,
      `dev-docs/library-analysis.md` and ADR 0001. Leave `dev-docs/history/` (archival),
      the released `CHANGELOG.md` entry, and `dev-docs/threat-model.md`'s "a BCJ2 folder
      has four pack streams" lines, which stay true.
- [x] 3.3 `dev-docs/threat-model.md`: two rows for D6. One is the per-output-byte CPU
      cost, with the measured worst case. The other is memory: a BCJ2 folder's three
      LZMA dictionaries add up, no `DecoderLimits` guard sees them yet, and the guard's
      unit must be the folder when it lands.
- [x] 3.4 In `dev-docs/IDEAS.md`, on the entry "Threat-model row for archive-declared
      decoder memory" (which tracks the LZMA guard), add that a BCJ2 folder's branch
      dictionaries count together, so the guard's unit is the folder. The guard is
      not built here.
- [x] 3.5 `CHANGELOG.md`: BCJ2 7z folders now read.
- [x] 3.6 Delete `prototype/` from this change directory.

## 4. Finish

- [x] 4.1 Run the three configs from CONTRIBUTING.md's "Before pushing…" rule.
- [x] 4.2 `openspec archive sevenzip-bcj2-decode --yes`, and commit the `openspec/specs/`
      diff in the same PR.
