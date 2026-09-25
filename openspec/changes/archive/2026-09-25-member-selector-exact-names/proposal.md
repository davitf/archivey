# Member selector names match exactly

## Why

`member-selector-spellings-and-unmatched` let a `members=` name without a trailing `/`
also select the directory spelling, so `"dir"` selected `dir/`. `get()` and `open()`
kept exact matching, so the same string meant different things in different methods.
The maintainer chose consistency: every caller-written name matches exactly for now,
because it is easier to widen a rule later than to narrow it.

## What changes

- `archive-reading`: a `str` selector entry matches the stored name exactly again. The
  directory spelling (`"dir/"`) is required, the same as for `get()` and `open()`.
- The `MEMBER_SELECTOR_UNMATCHED` diagnostic stays. `members=["dir"]` against `dir/` is
  now reported by it, so the missing `/` is not silent.
- Link-target lookup keeps trying both spellings. A stored link target is archive data,
  not a name the caller wrote.

## Impact

Code: `internal/selection.py`. Docs: `docs/opening-and-listing.md`. Tests:
`tests/test_member_selector.py`.
