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

## 7. Review round (PR #212)

- [x] 7.1 **The install hints were only half-migrated, and the half that was missed is
      the user-visible one.** `MissingComponent.install_hint` / `INSTALL_HINT` were
      updated, so listing and `format_availability()` looked correct — but every
      `raise PackageNotInstalledError(...)` carried its *own* hardcoded copy of the hint
      and still advertised deleted extras. A caller hitting the original bug (a ZIP with
      a Deflate64 or PPMd member) was told to `pip install archivey[7z]`, which after
      this change fails outright. Same for `[crypto]` (AES in `crypto.py` and both
      `zip_aes.py` sites), `[iso]` (the ISO reader — whose own backend constant already
      said `[recommended]`), `[lz4]`, `[zstd]`, and `[7z]` for pybcj.
- [x] 7.2 Fixed at the root rather than string-by-string: `MissingComponent.message()`
      now formats the error text, and every raise site builds its message from the same
      declared requirement it reports through. Codecs get `StreamCodec._missing()`;
      crypto, pybcj and pycdlib get a module-level `MissingComponent`; the ISO backend's
      `OPTIONAL_DEPENDENCY` / `INSTALL_HINT` are derived from that same object, so the
      two channels are now the same object rather than two strings that agree by luck.
      The four rapidgzip raises (correct already, but a third copy of the hint) route
      through `_RAPIDGZIP_REQUIREMENT`.
- [x] 7.3 The zstd message keeps its extra sentence — the backport is a no-op on 3.14+,
      where the stdlib module is used — via a `note=` argument rather than a bespoke
      string, so the hint part still comes from the requirement.
- [x] 7.4 Red-green regression tests. `test_absent_codec_backend_hint_is_installable`
      forces each backend global to `None` so the raise is exercised **in every
      dependency leg**, including the one where the package is installed — which is
      exactly where this rotted unnoticed. Plus the ISO reader raise (patching only
      `iso_reader.pycdlib`, so dispatch reaches the backend instead of stopping at the
      registry gate), the pybcj raise, and the crypto wrapper. Verified failing against
      the pre-fix tree: 7 of the 8 new tests fail, and the one that passes is the
      requirement-hint channel — the half that was already correct.
- [x] 7.5 `test_no_source_file_advertises_a_deleted_extra` greps `src/` for the deleted
      names. The behaviour tests only cover raises a test happens to reach; this fails on
      any new hardcoded hint anywhere, which is the failure mode that produced 7.1.
- [x] 7.6 Delta amended: the requirement that hints name a real extra already existed and
      the code simply violated it, so nothing needed loosening. Added the missing
      normative bit — both channels MUST derive from one declared requirement per package
      — plus a scenario row asserting they produce the same string.
- [x] 7.7 **Task 2.3's "tree-wide grep clean" claim was wrong.** Swept the survivors
      outside `docs/grab-bag/`: 8 prose sites in `src/` (CLI, ISO, crypto, backends and
      streams module docstrings), `docs/internal/library-analysis.md` (11 — linked from
      the published `formats.md` as codec rationale), `docs/internal/known-issues.md`,
      `docs/internal/threat-model.md`, `PLAN.md` (7, including the extras roster in the
      Phase 1 task list), `IDEAS.md`, and 8 test comments. `docs/grab-bag/`,
      `review/archive/**` and the ADR parentheticals stay as historical record.
- [x] 7.8 Three-config suite re-run green (`[all]`, `[all-lowest]`, core-only);
      `ruff`, `pyrefly`, `ty`, `mkdocs build --strict`, `openspec validate --all` clean.

## 8. Open for the maintainer — where the remaining stale prose lives

§6's nine-spec question is unchanged and still needs a call. The review raised a second,
narrower one: whether `docs/internal/*` and `PLAN.md` belonged in this PR at all, since
task 2.3 only ever exempted `docs/grab-bag/`.

Resolved as **A (do it here)** rather than surfaced, because unlike §6 it is not a
judgement call about scope boundaries: `library-analysis.md` is linked from the published
`formats.md` as the codec rationale, and `PLAN.md` enumerated the *old eleven extras* as
the packaging to build — wrong instructions for the next agent that reads it. Both are
prose edits with no spec or behaviour implication. Say if you would rather these had been
split out.
- [x] 7.9 **`uv.lock` carried an unrelated dependency refresh** — 30 package versions had
      been bumped alongside the structural extras change, including `ty` 0.0.60 → 0.0.65,
      which flags five pre-existing `os.fspath` overload diagnostics in
      `decompressor_stream.py` and would have failed CI's type-check job for reasons
      unrelated to this change. Relocked from the base lockfile: the diff is now the
      extras restructure only, with zero version bumps.
