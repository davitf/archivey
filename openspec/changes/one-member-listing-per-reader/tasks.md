## 0. Gate

- [ ] 0.1 Before archiving, check the `access-mode-and-cost` MODIFIED block against the live
      requirement. A MODIFIED delta replaces the whole block, so anything the live
      requirement gained after this change was written would be deleted silently. Dry-run
      `openspec archive` on a scratch copy of `openspec/`, `diff -u` the result against the
      live specs, and confirm every removed line is one this change means to replace.

## 1. Red tests first

- [ ] 1.1 Failing test: on a fresh 7z and a fresh solid RAR, in both access modes, every
      member yielded by `stream_members()` has `member_id` set and `member in reader` is
      true
- [ ] 1.2 Failing test: for ZIP, ISO, 7z and RAR, `members_report_if_available()` twice and
      then `members()` return the same objects (`is`), and a spy on `_iter_members` counts
      one call
- [ ] 1.3 Failing test: `extract_all()` on ZIP, ISO, 7z and RAR walks `_iter_members` once
- [ ] 1.4 Failing test: a ZIP symlink member held from a peek gets its `link_target` set in
      place after `members()`
- [ ] 1.5 Failing test: `MEMBER_NAME_BIDI_CONTROL` from a peek-then-materialize sequence is
      counted once and attached to the object the caller holds (today it attaches to the
      discarded prep-pass object)
- [ ] 1.6 Failing test: streaming `extract_all()` over a ZIP holding `a.txt` twice returns
      `SUPERSEDED`, `EXTRACTED` for the two entries, as random access does (today it raises
      `ExtractionError: Destination already exists`)
- [ ] 1.7 Failing test: over a streaming ZIP with a symlink, peek inside the
      `stream_members()` loop and `break`; assert `_materialized` stays unpublished and no
      link-target read happened (D1b). Mutation: finalize on walk exhaustion; the test
      must go red.
- [ ] 1.8 Failing test: a typing-time diagnostic (normalized name, invalid timestamp) on
      ZIP, 7z, RAR and ISO carries a context `member_id` equal to the member's own
      `member_id` (D8)
- [ ] 1.9 Keep green, and check each fails when its path is broken:
      `MEMBER_NAME_NORMALIZED` counted once per member on `extract_all` (ZIP),
      the existing PR 386 dedupe tests, and the listing-limit refusal part-way through a
      ZIP listing (`tests/test_listing_limits.py`)

## 2. One walk in the base (D1, D2, D4)

- [ ] 2.1 Add the base-owned walk: `_listed`, its name index, the `_iter_members()`
      iterator, and the walk outcome. One pull method stamps, runs presentation checks,
      accounts, indexes and appends.
- [ ] 2.2 Route `members_report_if_available()` through the walk (pull to end, apply
      last-entry-wins once) and delete `_get_members_index_only`
- [ ] 2.3 Route `_materialize_members` through the walk; resolve links on `_listed`;
      publish `_materialized` from it
- [ ] 2.4 Route `_begin_forward_pass` / `_ProgressivePassIterator` through the walk;
      fold `_pass_scanned` / `_pass_by_name_lists` into the base list. The pass keeps its
      own cursor and finalizes when that cursor passes the end of a completed walk (D1b).
- [ ] 2.5 Stamp last-entry-wins `is_current` once, at walk completion (D1a). Drop
      `_finalize_links`' `is_current_first` parameter and its "preserve those orderings"
      docstring note.
- [ ] 2.6 Failure handling per D2: discard and allow a retry in random access, poison in
      streaming, store the incomplete report on terminal damage. One test per branch
      that fails when a branch is routed to the wrong behaviour.
- [ ] 2.7 Listing limits per D4: account once at pull, enforce per caller, and run
      `assert_within_limits()` when an enforcing caller finds members already pulled.
      Remove the tracker `reset()` calls and `_register_member`'s "already stamped"
      branch.

## 3. Concurrency (D5)

- [ ] 3.1 Put the walk under the first-touch election. Test a peek racing `members()`
      under `concurrent_members=True`: one walk, same objects, no `ArchiveyUsageError`.
- [ ] 3.2 Test a peek from inside a streaming `for` loop over an upfront-index backend:
      the pass continues and yields the same objects the peek returned

## 4. Backends (D6)

- [ ] 4.1 `sevenzip_reader._iter_with_data`: iterate the base list, not `self._members`
- [ ] 4.2 `rar_reader._iter_with_data` solid branch: the same
- [ ] 4.3 ZIP and 7z: drop the `report_key=` arguments and emit directly. ZIP keeps the
      `index` parameter (D8 uses it).
- [ ] 4.4 7z folder sweep (D6b): resolving any link in a folder decodes that folder once,
      up to its last link member, and fills every link in it. Random-access listing and
      the streaming pass's EOF finalization both use it, and the streaming pass feeds it
      from the decode it is already doing. Pin the D6a numbers as tests on a solid 7-Zip
      `-snl` fixture with links mid-folder: `members()` decodes up to the last link's end
      offset (111 100 bytes on the D6a tree, not 273 278), and a streaming pass decodes
      nothing beyond its own data. Mutation: resolve per link again; both counts must
      rise.
- [ ] 4.5 Commit that fixture: 7-Zip `-snl` output over a tree with links between
      files, since the corpus's 7z fixtures come from py7zr and none has a link mid-folder
- [ ] 4.6 D8: 7z, RAR and ISO pass the listing position into typing-time diagnostic
      contexts as `member_id` (7z and RAR: the index in their open-time list; ISO: an
      `enumerate` over its walk)

## 5. Delete the dedupe machinery (D7)

- [ ] 5.1 Remove `_member_reports`, the memo half of `_report_member_diagnostic`,
      `DiagnosticCollector.reattach_to_member` and `_presentation_checked`
- [ ] 5.2 Rewrite `_iter_members`' docstring: runs once per completed walk; order stability
      still required for a retried random-access walk

## 6. Docs

- [ ] 6.1 `dev-docs/known-issues.md`: mark the ISO typing-time-diagnostics entry resolved
      by this change
- [ ] 6.2 `dev-docs/IDEAS.md`: remove "Reuse the index-only pass's members instead of
      rebuilding them"
- [ ] 6.3 `dev-docs/code-map.md`: rewrite "Listing can happen twice…" to describe the one
      walk, and drop the "dedupe on the member id, never on object identity" advice where
      it no longer applies
- [ ] 6.4 `docs/`: check the user-facing pages for any sentence implying a peek returns
      snapshot objects

## 7. Verify and archive

- [ ] 7.1 `openspec validate --strict one-member-listing-per-reader`
- [ ] 7.2 `./scripts/check.sh` and the three test configs from CONTRIBUTING.md
      "Before pushing…"
- [ ] 7.3 `openspec archive one-member-listing-per-reader --yes` after task 0.1, and commit
      the resulting `openspec/specs/` diff in the same PR
