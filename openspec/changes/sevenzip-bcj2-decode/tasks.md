## 0. Gate

- [ ] 0.1 Before archiving, check all seven MODIFIED blocks against the live
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
- [ ] 0.2 Answer design open question 1 if an older 7-Zip is available (does its output
      leave `rc` bytes or a non-zero final `code`?). If not, keep D5 as written.

## 1. Red tests first

- [ ] 1.1 Fixture helper: build BCJ2 archives with the `7z` CLI (`chmod +x` the input
      before `-mx9`, or the forced `-m0=BCJ2 …` form), skipping when the CLI is absent.
      Cover `-mx9` single, `-mx9` solid, `-ms=off`, encrypted with `-mhe=on`, and forced
      BCJ2 on inputs ending in `E8` and in `0F`.
- [ ] 1.2 Failing tests: each fixture reads byte-for-byte equal to its input, through
      `open()`, `stream_members()` and `extract_all()`.
- [ ] 1.3 Failing test: `stream_members()` over the solid fixture opens the folder's pack
      streams once (count views or seeks on the source).
- [ ] 1.4 Failing tests on `Bcj2DecoderStream` directly: each input truncated by one byte
      raises `TruncatedError`; one extra byte in `main`, `call` or `jump` raises
      `CorruptionError`; input block sizes 1, 7 and 64 KiB give the same output.
- [ ] 1.5 Failing test: a folder whose coder output is bound twice, or whose bind pairs
      form a cycle, raises the error D1 names, with no output bytes.
- [ ] 1.6 Replace `test_bcj2_folder_is_rejected` with a test that a multi-input coder
      other than BCJ2 is rejected. Keep
      `test_bcj2_nonsolid_pack_streams_are_not_member_scaled`.

## 2. Implement

- [ ] 2.1 Move `prototype/bcj2.py` to `src/archivey/internal/streams/bcj2.py`. Add the D5
      end-of-output checks for `main`, `call` and `jump`. Classify the new stream in the
      four `tests/test_stream_bases.py` inventories.
- [ ] 2.2 `plan_folder`: resolve the tree (D1). Linear folders keep their current plan
      exactly; add a test that pins that, over the existing fixtures.
- [ ] 2.3 `open_folder_pipeline`: take one view per pack stream, run each branch's stages,
      and put `Bcj2DecoderStream` over the four branch outputs. The BCJ2 stage owns and
      closes its branches.
- [ ] 2.4 `sevenzip_reader`: `_folder_pack_views` in place of `_folder_pack_view`, used by
      `_open_folder_stream` and by the password check.
- [ ] 2.5 Leave `decode_encoded_header` linear-only (D7), with a test.

## 3. Docs and records

- [ ] 3.1 `dev-docs/formats/7z.md`: the at-a-glance "Refuses" row, §1's BCJ2 paragraph,
      §3's `-mx9` table, the two §5 rows (the refusal and its message), the §6 decision
      row, §8's verify table, plus a §5 row for the speed cost (D3).
- [ ] 3.2 `docs/formats.md`, `AGENTS.md`, `openspec/project.md`, `dev-docs/PLAN.md`,
      `dev-docs/open-issues.md` and ADR 0001: drop "BCJ2 unsupported".
- [ ] 3.3 `dev-docs/threat-model.md`: a row for the per-output-byte CPU cost (D6), with
      the measured worst case.
- [ ] 3.4 The LZMA dictionary guard, whenever it lands, counts a folder's branches
      together (D6). Record this where that work is tracked. It is not built here.
- [ ] 3.5 `CHANGELOG.md`: BCJ2 7z folders now read.
- [ ] 3.6 Delete `prototype/` from this change directory.

## 4. Finish

- [ ] 4.1 Run the three configs from CONTRIBUTING.md's "Before pushing…" rule.
- [ ] 4.2 `openspec archive sevenzip-bcj2-decode --yes`, and commit the `openspec/specs/`
      diff in the same PR.
