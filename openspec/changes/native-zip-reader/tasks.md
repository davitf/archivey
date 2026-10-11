# Tasks — native ZIP reader

## 0. Before coding

- [ ] 0.1 The three ZIP fix PRs from the October code sweep have merged (trailing bytes
      after a codec's end, malformed Unicode Path field, comment decoding). Rebase on
      main; their tests are acceptance tests from here on.
- [x] 0.2 Questions A, B and C in `design.md` are answered (davi, 2026-10-10) and
      recorded in `dev-docs/formats/zip.md` §6 and `dev-docs/design-rules.md` DR-8.
- [ ] 0.3 `openspec validate --strict native-zip-reader`.
- [x] 0.4 Question D is answered (davi, 2026-10-10: a per-member field, working name
      `member_state_final`, settled in the stage 3 PR).

## 1. Parser (PR 1)

- [x] 1.1 `internal/backends/zip_parser.py`: `EndRecord`, `find_end_record`,
      `CentralEntry`, `CentralDirectoryWalk`, `LocalHeader`, `read_local_header`.
- [x] 1.2 ZIP64: extra field `0x0001` resolution order, ZIP64 locator and record, entry
      count past 65 535, offsets past 4 GiB (hand-built records, no large files).
- [x] 1.3 Stub prefix: `base` matches stdlib `concat` for an unadjusted SFX and is 0 for
      an adjusted one.
- [x] 1.4 Disk fields and the archive extra data record raise the existing refusals,
      including under ZIP64.
- [x] 1.5 Differential test: every ZIP in `tests/fixtures/`, every archive
      `tests/create_adversarial.py` builds and the sample corpus give the same entries
      as `zipfile.infolist()` wherever `zipfile` opens it, over the fields and with the
      exceptions `design.md` §"Behaviour that changes" lists; shown to fail against the
      three mutants named there.
- [x] 1.5a `EndRecord.trailing` counts the bytes after the record and its declared
      comment; a comment cut short is not trailing (tests for both).
- [x] 1.6 Walk findings equal what `_end_record_findings` reports today on the existing
      end-record tests.

## 2. Switch the reader (PR 2)

- [ ] 2.1 `ZipReader` reads through the parser; `import zipfile` leaves `src/`.
- [ ] 2.1a Add the 3.13+ row for a comment plus trailing bytes totalling 65 536 to the
  handbook's behaviour notes, as the design's §"Behaviour that changes" table records
  it (the search window stays stdlib's up to 3.12).
- [ ] 2.2 Reader-owned lock; the source is the handle for path sources too.
- [ ] 2.3 Lazy `_iter_members`; `member_count` from a complete walk, `None` when it fails
      or stops at `max_members`.
- [ ] 2.4 Offsets and count collected by the listing walk; the index pass only when a
      member open or `member_count` comes first; both charge `max_members` (tests:
      a listing then an open walks the directory once, counted reads).
- [ ] 2.4a Open refuses a declared entry count over `max_members` when `cd_size` can hold
      it; a count the directory cannot hold stays a finding (tests for both).
- [ ] 2.5 Damaged directory lists the entries before the damage, then raises (tests
      first: bad signature mid-directory, directory cut by end of file).
- [ ] 2.6 Version needed above 6.3 lists and reads (test first).
- [ ] 2.6a Trailing bytes after the end record report `ARCHIVE_TRAILING_DATA` as
      `design.md` §"Trailing bytes" states (1 MiB bound shared with TAR,
      `expected_marker="zeros_to_eof"`, zeros silent, strict refuses; the constant and
      zero-byte loop move to `internal/trailing_scan.py`); tests first,
      including a first non-zero byte past 1 MiB (silent) and a pipe.
- [ ] 2.7 Remove every row of `design.md` §"What the parser removes" except the name
      decode; tests that read `ZipInfo` off `member._raw` move to `CentralEntry`.
- [ ] 2.8 New ADR superseding 0006; format-zip spec; the `diagnostics` spec row and the
      `ArchiveEofContext` docstring for `"zeros_to_eof"`; `docs/gotchas.md` (trailing
      data, open pipe); `dev-docs/formats/zip.md` §2.2, §5, §6; `dev-docs/IDEAS.md`;
      `docs/formats.md`.
- [ ] 2.9 `./scripts/test.sh --all-configs`; benchmark listing against the baseline.

## 3. Streaming (PR 3)

- [ ] 3.0 A seekable source under `streaming=True` reads the directory first, then
      forward only (question C); a test counts its seeks.
- [ ] 3.0a Before the walk: open a real self-extractor fixture through a pipe under the
      `FAST` and default budgets and record what `payload_offset` comes back as.
- [ ] 3.1 `parse_local_header` / `parse_central_header` on bytes, shared by both walks;
      `LocalHeaderWalk` over a forward reader; data descriptor parsing (signature
      optional, 4- or 8-byte sizes).
- [ ] 3.2 Member end per row of `design.md` §"Where a member's data ends", including the
      STORED + bit 3 descriptor scan; tests with stdlib `zipfile` writing to a pipe.
- [ ] 3.3 End-of-pass reconciliation: members updated from the directory; extraction
      applies modes, symlinks and `OTHER` removal; unreferenced local entries removed
      and reported; a missing one is handled per 3.3g.
- [ ] 3.3a A retyped member goes through the filter and the policy checks again before
      its link is made; refused, its file is removed (test: a pipe ZIP whose directory
      types `payload` as a symlink to `../../etc/passwd` is refused from a pipe and from
      a file).
- [ ] 3.3b A size or CRC from a data descriptor that differs from the central entry is
      detected; its outcome is 3.3g's (test: a STORED bit-3 member that embeds a
      descriptor for a prefix of itself, read from a pipe and seekably).
- [ ] 3.3c `is_current` from the directory order; same-name members in reverse order are
      detected at the end of the pass, with 3.3g's outcome (fixture with the two orders
      reversed).
- [ ] 3.3d A stream that starts neither with a local header nor an end record: scan
      within `SFX_MAX` with `validate_zip_local_header`; nothing found is
      `CorruptionError` (tests for both).
- [ ] 3.3e Forward-pass `ArchiveInfo` and cost receipt as in `design.md` §"What the
      caller sees in a forward pass"; `ArchiveMember.member_state_final` (question D)
      in every format: false for a pipe ZIP member until the directory, for any member
      of a forward-only pass (TAR too) until the pass ends, and for a data-stored link
      target until it is read (a test for each).
- [ ] 3.3f A reparse-flagged member whose data is not a reparse buffer is typed when
      `stream_members()` reaches it and yields its whole content, as the 7z pass does
      (read-ahead stream); from a pipe it stays a file (tests in both modes, red
      first).
- [ ] 3.3g End-of-pass outcomes exactly as `design.md` §"Failures found at the end of
      the pass" states them, under `OnError.CONTINUE` and `STOP`, for 3.3b, 3.3c and a
      directory entry with no local entry. Each test checks every affected result and
      what is at the path afterwards (the reversed pair: `M1` `FAILED`, `M2`
      `SUPERSEDED`, the path empty); `stream_members()` raises for 3.3b and the missing
      entry, not for 3.3c.
- [ ] 3.3h `max_members` charged under `streaming=True`: the directory walk of a
      seekable source and the members a forward pass keeps (tests: a directory over
      the limit refuses in both).
- [ ] 3.4 Every fixture read through a pipe and seekably: same members after the pass,
      same files on disk, except the two divergences `design.md` names (3.3b, 3.3c),
      which 3.3g tests instead.
- [ ] 3.5 `SUPPORTS_STREAMING_NON_SEEKABLE = True`; the format-zip, `backend-registry`
      and `access-mode-and-cost` specs, `archive-reading` (ZIP applies `max_members` at
      parse) and the `ListingLimits` docstring in `config.py`, `archive-data-model` for
      `member_state_final` (and `safe-extraction` if the filter re-run needs a
      sentence); `docs/access-and-cost.md`, `docs/formats.md`; handbook §1, §2.2, §5,
      §6; `dev-docs/formats/tar.md`, `7z.md` and `rar.md` for which fields
      `member_state_final` covers there.
- [ ] 3.5a The other live statements of the two contracts stage 3 moves (grep
      `"7z, RAR and ISO"` and `"ZIP, 7z, RAR and ISO"` before finishing; the list below
      is the 2026-10-10 result):
      - `max_members` at parse, ZIP joins: `docs/gotchas.md` (§Limits; the
        `documentation` spec requires that residual there),
        `docs/opening-and-listing.md`, `docs/extracting.md` (the limits table and two sentences),
        `dev-docs/threat-model.md` (the recorded residual shrinks; say so),
        `exceptions.py` (`ResourceLimitError` docstring), `config.py` (both docstrings).
      - ZIP no longer has to seek: `docs/reading-members.md` (a member stream handed to
        `open_archive()`), `docs/opening-and-listing.md` (index at the end),
        `tests/test_nested_archives.py` (module docstring),
        `openspec/changes/bounded-source-spooling/proposal.md` if that change is
        still open.

## 4. Names (PR 4)

- [ ] 4.1 A flagged name that is not valid UTF-8 decodes as an unflagged name, with
      `MEMBER_NAME_ENCODING_INFERRED` (`declared_encoding="utf-8"`); tests from a real
      writer where one exists (DR-24), else hand-built.
- [ ] 4.2 Collisions stay ordinary duplicates (question B: keep); a test pins it.
- [ ] 4.3 Spec, handbook §2.2 and §5, `docs/formats.md`; the DR-21 ruling in
      `dev-docs/design-rules.md`.

## 5. Header disagreement (PR 5)

- [ ] 5.1 Cases 1 and 2 read, with a new diagnostic (name to the maintainer) and
      strict refusing; case 3 stays refused. Turn the `xfail(strict)` tests in
      `tests/test_audit_backup_scan.py` into the ruled behaviour.
- [ ] 5.2 Format-zip spec, `docs/errors-and-diagnostics.md`, handbook §5, the status of
      §3.3-3.4 in `dev-docs/investigations/2026-10-backup-scan.md`.

## 6. ZIPs over 4 GiB without ZIP64 (PR 6)

- [ ] 6.1 Offset correction in `CentralDirectoryWalk`, gated on a matching local header
      at the corrected offset; CRC kept; diagnostic.
- [ ] 6.2 Test with a small archive whose stored offsets are reduced by 2³².
- [ ] 6.3 Format-zip spec, handbook §3 and §5, `docs/formats.md`; close the `IDEAS.md`
      item.

## 7. Shrink and Implode (PR 7)

- [ ] 7.1 Pure-Python decoders for methods 1 and 6, registered with the codec layer.
- [ ] 7.2 Fixtures from a real writer (Info-ZIP `zip` 1.x / PKZIP 1.x output or the
      scan's archives, if their licence allows), checked against `unzip`.
- [ ] 7.3 The new `CompressionAlgorithm` members go to the maintainer before merging.
- [ ] 7.4 Format-zip spec, `docs/formats.md`, handbook §2.3.
