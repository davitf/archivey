# Tasks — native ZIP reader

## 0. Before coding

- [ ] 0.1 The three ZIP fix PRs from the October code sweep have merged (trailing bytes
      after a codec's end, malformed Unicode Path field, comment decoding). Rebase on
      main; their tests are acceptance tests from here on.
- [ ] 0.2 Open questions A and B in `design.md` have answers, or stages 3 and 4 wait.
- [ ] 0.3 `openspec validate --strict native-zip-reader`.

## 1. Parser (PR 1)

- [ ] 1.1 `internal/backends/zip_parser.py`: `EndRecord`, `find_end_record`,
      `CentralEntry`, `CentralDirectoryWalk`, `LocalHeader`, `read_local_header`.
- [ ] 1.2 ZIP64: extra field `0x0001` resolution order, ZIP64 locator and record, entry
      count past 65 535, offsets past 4 GiB (hand-built records, no large files).
- [ ] 1.3 Stub prefix: `base` matches stdlib `concat` for an unadjusted SFX and is 0 for
      an adjusted one.
- [ ] 1.4 Disk fields and the archive extra data record raise the existing refusals,
      including under ZIP64.
- [ ] 1.5 Differential test: every ZIP in `tests/fixtures/` and every archive
      `tests/create_adversarial.py` builds gives the same entries as
      `zipfile.infolist()` wherever `zipfile` opens it.
- [ ] 1.6 Walk findings equal what `_end_record_findings` reports today on the existing
      end-record tests.

## 2. Switch the reader (PR 2)

- [ ] 2.1 `ZipReader` reads through the parser; `import zipfile` leaves `src/`.
- [ ] 2.2 Reader-owned lock; the source is the handle for path sources too.
- [ ] 2.3 Lazy `_iter_members`; `member_count` from a complete walk, `None` when it fails.
- [ ] 2.4 Offset array for the overlap guard, built on first member open.
- [ ] 2.5 Damaged directory lists the entries before the damage, then raises (tests
      first: bad signature mid-directory, directory cut by end of file).
- [ ] 2.6 Version needed above 6.3 lists and reads (test first).
- [ ] 2.7 Remove every row of `design.md` §"What the parser removes" except the name
      decode; tests that read `ZipInfo` off `member._raw` move to `CentralEntry`.
- [ ] 2.8 New ADR superseding 0006; format-zip spec; `dev-docs/formats/zip.md` §2.2,
      §5, §6; `dev-docs/IDEAS.md`; `docs/formats.md`.
- [ ] 2.9 `./scripts/test.sh --all-configs`; benchmark listing against the baseline.

## 3. Names (PR 3)

- [ ] 3.1 A flagged name that is not valid UTF-8 decodes as an unflagged name, with
      `MEMBER_NAME_ENCODING_INFERRED` (`declared_encoding="utf-8"`); tests from a real
      writer where one exists (DR-24), else hand-built.
- [ ] 3.2 Collisions, per the answer to question B.
- [ ] 3.3 Spec, handbook §2.2 and §5, `docs/formats.md`.

## 4. Header disagreement (PR 4, after question A)

- [ ] 4.1 Turn the three `xfail(strict)` tests in `tests/test_audit_backup_scan.py`
      into the ruled behaviour.

## 5. ZIPs over 4 GiB without ZIP64 (PR 5)

- [ ] 5.1 Offset correction in `CentralDirectoryWalk`, gated on a matching local header
      at the corrected offset; CRC kept; diagnostic.
- [ ] 5.2 Test with a small archive whose stored offsets are reduced by 2³².

## 6. Shrink and Implode (PR 6)

- [ ] 6.1 Pure-Python decoders for methods 1 and 6, registered with the codec layer.
- [ ] 6.2 Fixtures from a real writer (Info-ZIP `zip` 1.x / PKZIP 1.x output or the
      scan's archives, if their licence allows), checked against `unzip`.
- [ ] 6.3 The new `CompressionAlgorithm` members go to the maintainer before merging.
