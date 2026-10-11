# DiagnosticPolicy as two code sets

## Why

`DiagnosticPolicy(overrides={code: disposition})` is awkward for the common case. Ignoring
one code takes a dict of enum to enum:
`DiagnosticPolicy(overrides={DiagnosticCode.PASSWORD_ARGUMENT_UNUSED: DiagnosticDisposition.IGNORE})`.
Adjusting a preset is worse. `strict()` and `pedantic()` were static methods returning a
new policy, so "strict, minus one code" needed `dataclasses.replace` and a dict merge
over `policy.overrides`.

## What changes

- `DiagnosticPolicy` has two keyword-only fields, `ignore` and `raise_on`. Each takes any
  iterable of codes (or their names / values) and holds a `frozenset`. A code in
  `raise_on` raises, a code in `ignore` is ignored, and every other code is collected.
  A code in both is refused with `ArchiveyUsageError`, and so is a bare string.
- `DiagnosticPolicy.STRICT` and `DiagnosticPolicy.PEDANTIC` are ordinary instances:
  `raise_on=ARCHIVE_INTEGRITY_CODES` and `raise_on=frozenset(DiagnosticCode)`. A custom
  policy is built from the same sets:
  `DiagnosticPolicy(raise_on=ARCHIVE_INTEGRITY_CODES - {DiagnosticCode.ARCHIVE_TRAILING_DATA})`.
- Removed before 0.2.0: `overrides=`, `default=`, `strict()`, `pedantic()`.
  `DiagnosticDisposition` stays: `resolve()` returns it and the delivery matrix is
  written in it.

Alternatives not taken:

- **Three lists (`ignore=`, `collect=`, `raise_on=`) with presets taking them as
  arguments.** `collect=` was needed only to take a code back out of a preset. Building
  policies from the exported sets makes it unnecessary.
- **Keep `default=`.** "Raise on everything but X" is `frozenset(DiagnosticCode) - {X}`,
  and `PEDANTIC` keeps its meaning: `frozenset(DiagnosticCode)` is every code of the
  installed version, so it still raises on codes a later release adds.

## Impact

- `diagnostics`: the policy contract and the presets requirement.
- `error-handling`: the enum-coercion table rows for `DiagnosticPolicy`.
- Code: `src/archivey/diagnostics.py`; references to `strict()` / `pedantic()` across
  `src/`, `tests/`, `docs/`, `dev-docs/` and the other specs become `STRICT` / `PEDANTIC`.
