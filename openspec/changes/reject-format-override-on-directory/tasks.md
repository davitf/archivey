## 1. Reject the conflict

- [x] 1.1 Guard in `core.py` before `resolved_format = ArchiveFormat.DIRECTORY`:
      raise `ArchiveyUsageError` when `format` is neither `None` nor `DIRECTORY`
- [x] 1.2 Message names the path and the requested format, and gives both ways out
      (an archive file, or `format=ArchiveFormat.DIRECTORY`)

## 2. Tests

- [x] 2.1 `test_conflicting_format_on_directory_raises` — `format=ZIP` on a directory
- [x] 2.2 `test_explicit_directory_format_is_accepted` — the non-regression half
- [x] 2.3 Existing directory tests still pass (no `format=` callers affected)

## 3. Spec and docs

- [x] 3.1 `archive-reading` "Opening an archive for reading" states the rule
- [x] 3.2 Three matrix rows: no `format=`, `format=DIRECTORY`, conflicting `format=`
- [x] 3.3 `docs/opening-and-listing.md` — rejected, not ignored
- [x] 3.4 Close **P8** in `dev-docs/open-issues.md`

## 4. Verify

- [x] 4.1 `openspec validate --strict reject-format-override-on-directory`
- [x] 4.2 Dry-run archive on a scratch tree; confirm `~1`, then reset
- [x] 4.3 Full suite green; `ruff`, `pyrefly` clean
