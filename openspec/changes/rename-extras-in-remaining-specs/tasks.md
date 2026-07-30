## 1. Archive the two merged changes first

- [x] 1.1 `openspec archive consolidate-optional-extras` — applies the four-extra table to
      `openspec/specs/packaging-and-extras/spec.md`, which `main` was still missing after
      #212 merged.
- [x] 1.2 `openspec archive member-stream-capability-booleans` — applies 11 requirement
      updates across six specs (#213). Both were `✓ Complete` but unarchived, so `main`'s
      specs still described the pre-merge API.
- [x] 1.3 Re-derive this change's deltas from the **post-archive** specs. Generating them
      against the pre-archive text produced deltas that no longer matched — caught when a
      substitution's match-count assertion failed.

## 2. Rename across the remaining specs

- [x] 2.1 `testing-contract` (2 requirements) — 7z/RAR fixture corpora and the WinZip AES
      degradation matrix.
- [x] 2.2 `archive-writing` (1 + prose) — the ZSTD-without-backend writer scenario.
- [x] 2.3 `cli` (1 + prose) — `tqdm` now arrives via `[recommended]`; the command itself
      stays stdlib-only, which the reworded rows now say explicitly.
- [x] 2.4 `format-rar` (1 + prose) — header-encrypted RAR5 needs `cryptography`, not a
      `[rar]`/`[crypto]` pair. The `unrar` binary requirement is untouched: no extra
      supplies it.
- [x] 2.5 `compressed-streams` (3) — the codec/dependency table and the AES stage.
- [x] 2.6 `backend-registry` (2) — availability matrix and the ISO install hint.
- [x] 2.7 `format-7z` (4 + prose + 1 rename) — the codec table, the `[7z]`-bundle
      requirement, and the pybcj staging requirement whose **title** named the extra.
- [x] 2.8 `format-zip` (2) — optional member codecs and WinZip AES.
- [x] 2.9 `format-iso` (prose only) — no requirement mentions an extra, so no delta file.
- [x] 2.10 `packaging-and-extras` (5 + prose) — see §3; #212's own delta left these behind.

## 3. Sites #212's delta missed in its own capability

- [x] 3.1 **Factual error, not a rename.** The zero-dependency-core requirement claimed
      RAR5 members carrying only Blake2sp hashes "still read without `[rar]`, but the
      Blake2sp integrity check is skipped with a diagnostic/warning". BLAKE2sp is computed
      natively on stdlib `hashlib` (`src/archivey/internal/hashing/blake2sp.py`, imports
      `hashlib` and nothing else), so the check runs in core and no package is involved.
      Corrected to say so. This is the same error #212 fixed in `docs/acknowledgements.md`
      and `docs/formats.md`; the spec was not updated with it, so the authority still
      carried the wrong claim.
- [x] 3.2 The CLI entry-points requirement still gated `tqdm` behind `[cli]`.
- [x] 3.3 The dependency-audit requirement used `[cli]` as its example of a dependency
      pinned ahead of its phase.
- [x] 3.4 The `fuzz`-group requirement listed `[recommended-lite]` among the extras
      `atheris` must not appear in.
- [x] 3.5 The packaging-audit matrix still ran `pip install archivey[zstd]`.
- [x] 3.6 The 7z-writing sentence still named a `[7z-write]` extra that was never shipped.

## 4. Deliberately left alone

- [x] 4.1 `packaging-and-extras`: the "Removed names (`[7z]`, `[rar]`, `[crypto]`, `[iso]`,
      `[zstd]`, `[lz4]`, `[cli]`, `[recommended-lite]`) MUST NOT be reintroduced as
      aliases" list. Renaming it would delete the requirement's content.
- [x] 4.2 `packaging-and-extras`: the `| pip install archivey[7z] | Fails: the extra no
      longer exists |` scenario row, for the same reason.
- [x] 4.3 `docs/grab-bag/` and `review/archive/**` — declared non-normative historical
      record in #212.

## 5. Verification

- [x] 5.1 All 62 substitutions assert an exact match count of 1 before applying; the
      generator fails loudly rather than silently skipping a drifted site.
- [x] 5.2 Delta bodies generated from the live specs, never retyped (see `design.md`).
- [x] 5.3 `openspec validate --strict rename-extras-in-remaining-specs` passes.
- [x] 5.4 **Dry-run archive**: applied to a scratch tree, then diffed the resulting
      `openspec/specs/` against the generator's intended text — content-identical for all
      ten specs (the tool normalizes some blank lines) — and confirmed the only stale
      extras left are the two deliberate mentions in §4. Tree reset afterwards.
- [x] 5.5 The dry run surfaced one thing worth knowing before archiving for real:
      `openspec archive` applies a RENAMED requirement as remove-then-append, so
      `format-7z`'s pybcj requirement **moves to the end of the spec**. Requirement count
      stays 12 and the body is byte-identical; only its position changes. Recorded in
      `design.md` so the reshuffle in that diff is not mistaken for an accident.
- [x] 5.6 No source or test change, so the three-config suite is untouched by this change;
      run once as a smoke check that the archive step did not disturb the tree.
