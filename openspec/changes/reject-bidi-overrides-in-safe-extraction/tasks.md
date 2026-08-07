# Tasks — reject bidi overrides during safe extraction

## 1. The two sets

- [x] 1.1 Rename `_BIDI_CONTROLS` to `BIDI_CONTROLS` (it is now consumed outside
      `naming.py`) and add `BIDI_REORDERING_CONTROLS` — U+202A–202E and U+2066–2069
      **only**, enumerated, not derived by subtraction.
- [x] 1.2 A comment at both, saying which is which and why the marks stay out of the
      reject set. This is the mistake the change exists to avoid.

## 2. The rejection

- [x] 2.1 `DeceptiveNameError(FilterRejectionError)` in `exceptions.py`; export it.
- [x] 2.2 `check_universal`: reject a bidi override/isolate in `member.name`, next to the
      NUL/absolute/`..` string checks, before any path resolution.
- [x] 2.3 Same for SYMLINK/HARDLINK `link_target`, beside the existing NUL/encodability
      target checks.
- [x] 2.4 Confirm it is not policy-gated — `TRUSTED` lifts portability transforms, not
      safety constraints.

## 3. Specs and docs

- [x] 3.1 `safe-extraction` (the rule + the two subsets), `error-handling` (the new
      subclass; the tree there was also missing `UnportableNameError`, fixed in passing),
      `testing-contract` (both branches).
- [x] 3.2 `docs/safe-extraction.md` and `docs/gotchas.md`.
- [x] 3.3 `CHANGELOG.md`.

## 4. Tests

- [x] 4.1 An override in a name → `DeceptiveNameError`, nothing written, under every
      policy including `TRUSTED`.
- [x] 4.2 An isolate (U+2066) too.
- [x] 4.3 An override in a symlink target.
- [x] 4.4 **A directional mark extracts normally** — the regression this change is most
      at risk of causing.
- [x] 4.5 Arabic script with no controls extracts and emits nothing.
- [x] 4.6 Under `OnError.CONTINUE` it is a `BLOCKED` result like any other filter
      rejection.

## 5. Verify

- [x] 5.1 `openspec validate --strict reject-bidi-overrides-in-safe-extraction`
- [x] 5.2 Three dependency configs, `ruff`, `pyrefly`, `ty`.
