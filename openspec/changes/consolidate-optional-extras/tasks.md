## 1. Packaging

- [x] 1.1 Rewrite `[project.optional-dependencies]` to the four extras, with
      `cryptography; python_version >= "3.14"` inside `[free-threaded]`
      (`recommended`, `seekable`, `free-threaded`, `all`); delete `7z`, `rar`, `crypto`,
      `iso`, `zstd`, `lz4`, `cli`, `recommended-lite`.
- [x] 1.2 Drop the stale `[rar]` TODO; BLAKE2sp is native
      (`internal/hashing/blake2sp.py`), so nothing is pending.
- [x] 1.3 Verify `uv build` + `twine check --strict` still pass and the built METADATA
      lists exactly the four extras. *(Done: METADATA shows `all`, `free-threaded`,
      `recommended`, `seekable` and nothing else; hatchling flattens
      `archivey[recommended,seekable]` into `[all]`'s own requirement list.)*

## 2. Install hints

- [x] 2.1 Update every `MissingComponent` hint: `archivey[7z]` -> `archivey[recommended]`
      (`streams/codecs.py:1455`, `:1523`, `:1579` and any others found by grep).
      *(Also zstd/lz4 hints, `iso_reader.INSTALL_HINT`, and the two doc-comment examples
      in `types.py` / `base_reader.py`.)*
- [x] 2.2 rapidgzip hints -> `archivey[seekable]`. *(No string to change: rapidgzip is
      surfaced through `ArchiveStream`'s `suggest_install` warning, which names the
      package rather than an extra.)*
- [x] 2.3 Grep the whole tree (`src/`, `tests/`, `docs/`, `benchmarks/`, `scripts/`,
      `.github/`) for the removed extra names; none may survive outside archived
      history. *(`docs/grab-bag/` is deliberately untouched — it is declared
      non-normative historical prose, and rewriting a historical document to match a
      later layout is how provenance stops being trustworthy.)*

## 3. CI

- [x] 3.1 Free-threaded job: replace the explicit
      `--extra iso --extra zstd --extra lz4 --extra cli` list with
      `--extra free-threaded`; keep the GIL assertion.
- [x] 3.2 Confirm `--extra all` legs are unchanged and green. *(`[all]` still resolves to
      the same nine packages plus rapidgzip; all three matrix legs run green locally.)*

## 4. Docs

- [x] 4.1 `docs/formats.md` quick matrix: replace the per-format extra column with the
      new names, and state plainly that RAR *data* needs the `unrar` binary, which no
      extra provides.
- [x] 4.2 `docs/support-matrix.md`: point the free-threaded section at
      `[free-threaded]`, keeping the measured GIL table.
- [x] 4.3 `docs/usage.md` install block; README install line if affected. *(README's
      install line was already extra-free.)*
- [x] 4.4 `docs/acknowledgements.md:57-73` — the extras→packages table is a full
      restatement of the old scheme (8 rows, plus a line calling `[recommended]` /
      `[recommended-lite]` / `[all]` "convenience aliases"). Rewrite it for the four
      extras. Same edit drops "(Blake2sp backend still TBD)" — the published twin of
      the `pyproject.toml` TODO that task 1.2 removes. Fix the same conflation at
      `docs/formats.md:16,101`, which credit the `[rar]` extra with Blake2sp
      verification; it is native and needs no extra
      (`review/docs/observations.md` O-14).
- [x] 4.5 **Decide, don't grep, for the ADRs.** Task 2.3's "none may survive" sweep
      hits four decision records that state consequences in terms of extras being
      deleted: `0001` ("Optional `[7z]` covers…"), `0002` (`[crypto]` / `[rar]`),
      `0008` (`[seekable]` — survives), `0009` (`[zstd]`).
      **Decided: amend in place, minimally**, following the precedent set by
      `member-stream-capability-booleans` task 3.2 (which amends ADR 0003). Each edit
      names the current extra and appends a parenthetical recording the original name
      and that the consolidation happened before `0.2.0`, so the record stays honest
      about what was decided *then* while not advising an extra that no longer exists.
      `0008` needed no change.

## 5. Verification

- [x] 5.1 `pip install archivey[free-threaded]` resolves on 3.13t **and** 3.14t (with
      `cryptography` only on the latter) and the GIL stays
      disabled (already asserted by CI; confirm locally too).
      *(3.13t confirmed locally: resolves to pycdlib/lz4/tqdm/backports.zstd, no
      cryptography, `sys._is_gil_enabled()` False after importing all four plus
      `archivey`. 3.14t is asserted by the marker plus the measurement recorded in
      `design.md`; not re-run locally.)*
- [x] 5.2 Three-config suite green (`[all]`, `[all-lowest]`, core-only).
      *(1995 passed / 58 skipped, 1995 passed / 58 skipped, 1590 passed / 406 skipped.)*
- [x] 5.3 `mkdocs build --strict` clean. *(Green. It still lists the six pages missing
      from the nav — pre-existing, tracked as F5 in the docs IA review.)*

## 6. Open for the maintainer — spec references beyond `packaging-and-extras`

Surfaced rather than resolved, per `CLAUDE.md`'s pause-and-ask rule.

Ten authoritative specs name the removed extras, **79 references** in total:
`packaging-and-extras` (rewritten by this change's delta) plus `testing-contract`,
`archive-writing`, `cli`, `format-rar`, `format-7z`, `format-zip`, `format-iso`,
`compressed-streams`, `backend-registry`. They are behaviour requirements whose
*behaviour* is unchanged — only the extra's name moved — e.g. "`[crypto]` not installed |
`PackageNotInstalledError`; member still identified as encrypted".

This change's proposal scopes a delta to `packaging-and-extras` alone. Amending nine more
specs means nine MODIFIED-requirement deltas reproducing full requirement text, which is a
much larger change than the one proposed. The options:

| | Approach | Cost |
|---|---|---|
| **A** | Follow-up change that renames the extras across the other nine specs | Keeps this PR reviewable; leaves the specs stale until it lands |
| **B** | Expand this change with nine more spec deltas | One atomic change, no stale window; a large and mostly mechanical diff |
| **C** | Leave them; treat the names as descriptive rather than normative | Free, and wrong — the specs are the authority, so a reader would install an extra that does not exist |

**Recommendation: A**, and it should land before `0.2.0` rather than being deferred
indefinitely. C is not viable. B is defensible if you would rather not carry a stale
window at all.

`openspec/project.md` is cross-cutting context rather than an authoritative spec, so it
**was** updated here.
