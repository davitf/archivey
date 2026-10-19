# Tasks — stage, then place, a ZIP extracted from a pipe

## 0. Before coding

- [ ] 0.1 The maintainer rules on the two Open Questions in `design.md`: staged output and
      ADR 0010, and `members_done` moving only at placement.
- [ ] 0.2 `openspec validate --strict pipe-zip-stage-then-commit`.

## 1. Groundwork (no behaviour change)

- [ ] 1.1 Extract the forward pass's per-member body (`_run_pass`'s loop body) into one
      function that takes a member and its stream, and run the forward pass through it.
      The full suite passes unchanged.
- [ ] 1.2 Add the staged-file landing to `_write_file_atomic` (Decision 4) behind a
      parameter nothing passes yet, with a unit test: metadata applied, bytes not
      counted again, the source name gone after the landing.

## 2. Staging

- [ ] 2.1 For a reader with `_REVISES_YIELDED_MEMBERS`, the pass stages each member the
      selection may take into `<dest_root>/.archivey-tmp-<uuid>/<index>` (Decision 2):
      no filter call, recorded-size check before decoding, bomb limits counted when
      staged.
- [ ] 2.2 A member's data failure during staging is recorded and decided at placement,
      not raised. A test: an unlisted local entry whose data fails does not stop an
      `OnError.STOP` run.
- [ ] 2.3 Remove the staging directory in the run's `finally`, opening up modes first; a
      refused removal stays, with a warning (test with a read-only staged file on
      Windows, and an unremovable directory via a patched `rmdir`).

## 3. Placement

- [ ] 3.1 After the walk, place the final listing in order through the function from
      1.1: staged file as the stream, `_PassedData` where nothing was staged, the
      end-of-walk failures and missing members as results in listing order
      (Decision 3).
- [ ] 3.2 Refund from the byte cap and the entry count each staged member that is not
      placed; the archive-wide ratio keeps it (Decision 5).
- [ ] 3.3 Progress: intra-member reports while staging, the terminal report and
      `members_done` at placement (Decision 5).

## 4. Delete the revision machinery

- [ ] 4.1 Remove the functions, `_RunState` fields and `_MemberState` fields Decision 7
      names, and every call to them. Nothing outside `internal/extraction.py` refers to
      them; check with `grep`.
- [ ] 4.2 Re-read every comment in `internal/extraction.py` that names a removed function
      or describes revising a written member; `scripts/review_prep.py` sweeps the docs.

## 5. Specs and docs

- [ ] 5.1 Update `tests/` that pin today's pipe-ZIP behaviour to the delta specs: the
      stopped-run take-back, the caller's entries moved aside, the filter called twice,
      the `docs\r.txt` filter case.
- [ ] 5.2 `docs/extracting.md`: the leftover note covers a `.archivey-tmp-*` directory,
      and the pipe-ZIP section states what a stopped run leaves.
- [ ] 5.3 `dev-docs/formats/zip.md` and `native-zip-reader/design.md`: point the
      end-of-pass section at this change for the extraction side.

## 6. Verify

- [ ] 6.1 Every scenario row in the three delta specs has a test; the changed rows fail
      on `main` (`scripts/review_prep.py red-on-base`).
- [ ] 6.2 `./scripts/check.sh`, `./scripts/test.sh` and `./scripts/test.sh --all-configs`.
- [ ] 6.3 `openspec validate --strict pipe-zip-stage-then-commit`.
