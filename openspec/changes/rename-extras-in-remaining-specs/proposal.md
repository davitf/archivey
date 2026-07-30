## Why

`consolidate-optional-extras` (#212) collapsed eleven extras into four, but scoped its
spec delta to `packaging-and-extras` alone. Ten other authoritative specs still name
`[7z]`, `[crypto]`, `[iso]`, `[zstd]`, `[lz4]`, `[cli]`, `[rar]`, `[recommended-lite]`
and `[7z-write]` — 62 references. The specs are the authority, so a reader following
them is told to run `pip install archivey[7z]`, which now fails outright.

Behaviour does not change anywhere in this proposal. Only the names do, plus one
factual correction listed below.

## What Changes

- Rename the removed extras to `[recommended]` (or `[seekable]`) across the remaining
  specs, or to the **package** name where that is what the sentence is really about
  (`cryptography`, `pyppmd`, `pybcj`, `lz4`, `backports.zstd`, `pycdlib`, `tqdm`).
- Reword the places where two format-named extras collapse into one, so the text does
  not read as though `[recommended]` were being installed twice.
- Rename one requirement whose *title* names a deleted extra
  (`format-7z`: "Stage LZMA1+BCJ through pybcj under `[7z]`").
- Fix five sites in `packaging-and-extras` that #212's own delta missed, including one
  **factual error, not a rename**: the spec said RAR5 members carrying only Blake2sp
  hashes "still read without `[rar]`, but the Blake2sp integrity check is skipped with a
  diagnostic/warning". BLAKE2sp is computed natively on stdlib `hashlib`
  (`src/archivey/internal/hashing/blake2sp.py`), so nothing is skipped and no package is
  involved. This is the same error #212 corrected in the published docs; the spec was
  not updated with it.
- Leave the deliberate historical mentions intact: the "Removed names … MUST NOT be
  reintroduced as aliases" list and the `pip install archivey[7z]` → *fails* scenario row
  both need the old names to say what they say.

## Capabilities

### New Capabilities

*(none)*

### Modified Capabilities

- `packaging-and-extras`
- `testing-contract`
- `archive-writing`
- `cli`
- `format-rar`
- `format-7z`
- `format-zip`
- `format-iso` *(prose only — no requirement changes; see design.md)*
- `compressed-streams`
- `backend-registry`

## Impact

No source, test, or packaging change: `pyproject.toml` already ships the four extras and
every install hint already names them (#212). This proposal moves the specs to match what
shipped.

Docs are unaffected — the published pages were swept in #212. `docs/grab-bag/` and
`review/archive/**` keep the old names as historical record, as decided there.
