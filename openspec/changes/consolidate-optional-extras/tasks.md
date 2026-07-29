## 1. Packaging

- [ ] 1.1 Rewrite `[project.optional-dependencies]` to the four extras, with
      `cryptography; python_version >= "3.14"` inside `[free-threaded]`
      (`recommended`, `seekable`, `free-threaded`, `all`); delete `7z`, `rar`, `crypto`,
      `iso`, `zstd`, `lz4`, `cli`, `recommended-lite`.
- [ ] 1.2 Drop the stale `[rar]` TODO; BLAKE2sp is native
      (`internal/hashing/blake2sp.py`), so nothing is pending.
- [ ] 1.3 Verify `uv build` + `twine check --strict` still pass and the built METADATA
      lists exactly the four extras.

## 2. Install hints

- [ ] 2.1 Update every `MissingComponent` hint: `archivey[7z]` -> `archivey[recommended]`
      (`streams/codecs.py:1455`, `:1523`, `:1579` and any others found by grep).
- [ ] 2.2 rapidgzip hints -> `archivey[seekable]`.
- [ ] 2.3 Grep the whole tree (`src/`, `tests/`, `docs/`, `benchmarks/`, `scripts/`,
      `.github/`) for the removed extra names; none may survive outside archived
      history.

## 3. CI

- [ ] 3.1 Free-threaded job: replace the explicit
      `--extra iso --extra zstd --extra lz4 --extra cli` list with
      `--extra free-threaded`; keep the GIL assertion.
- [ ] 3.2 Confirm `--extra all` legs are unchanged and green.

## 4. Docs

- [ ] 4.1 `docs/formats.md` quick matrix: replace the per-format extra column with the
      new names, and state plainly that RAR *data* needs the `unrar` binary, which no
      extra provides.
- [ ] 4.2 `docs/support-matrix.md`: point the free-threaded section at
      `[free-threaded]`, keeping the measured GIL table.
- [ ] 4.3 `docs/usage.md` install block; README install line if affected.
- [ ] 4.4 `docs/acknowledgements.md:57-73` — the extras→packages table is a full
      restatement of the old scheme (8 rows, plus a line calling `[recommended]` /
      `[recommended-lite]` / `[all]` "convenience aliases"). Rewrite it for the four
      extras. Same edit drops "(Blake2sp backend still TBD)" — the published twin of
      the `pyproject.toml` TODO that task 1.2 removes. Fix the same conflation at
      `docs/formats.md:16,101`, which credit the `[rar]` extra with Blake2sp
      verification; it is native and needs no extra
      (`review/docs/observations.md` O-14).
- [ ] 4.5 **Decide, don't grep, for the ADRs.** Task 2.3's "none may survive" sweep
      hits four decision records that state consequences in terms of extras being
      deleted: `0001` ("Optional `[7z]` covers…"), `0002` (`[crypto]` / `[rar]`),
      `0008` (`[seekable]` — survives), `0009` (`[zstd]`). ADRs are dated records, so
      the choice is amend-in-place vs. leave-and-supersede. `member-stream-capability-booleans`
      task 3.2 amends ADR 0003 in place, so precedent says amend — but make it the
      explicit call rather than a side effect of a grep.

## 5. Verification

- [ ] 5.1 `pip install archivey[free-threaded]` resolves on 3.13t **and** 3.14t (with
      `cryptography` only on the latter) and the GIL stays
      disabled (already asserted by CI; confirm locally too).
- [ ] 5.2 Three-config suite green (`[all]`, `[all-lowest]`, core-only).
- [ ] 5.3 `mkdocs build --strict` clean.
