# Design: remove the top-level `extract()`

Stub. The reasons and what was weighed are in
`dev-docs/decisions/0019-no-top-level-extract.md`.

Tests that only needed "open, then extract everything" call a test-only helper,
`tests/extract_util.open_and_extract`, which is the two-line pattern and nothing more.
Tests that pinned behaviour only `extract()` had were removed: the detect-and-open
report scope, the automatic streaming switch (now a test that passes `streaming=True`),
and the argument checks that named `extract()` (the same checks still run on
`open_archive()` and `extract_all()`).
