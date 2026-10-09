# Error Handling — review guide

Optional. The rules are in `CONTRIBUTING.md` §Coding standards ("Exception translation
is specific", "Picking an exception type means checking what catches it"), and
[`code-pr.md`](../code-pr.md) §Coding and contract checks says which of them PRs break.
This page is the mechanism a reviewer traces when a finding touches it.

## Translation

Each backend overrides `_translate_exception(exc) -> ArchiveyError | None`
(`internal/base_reader.py`). `_raise_translated` and `_translated_errors` are the one
boundary that calls it: an `ArchiveyError` is stamped with the member and re-raised, a
recognized exception is raised as its translation `from exc`, and anything the
translator returns `None` for propagates unchanged.

- [ ] One `isinstance` branch per known failure of the third-party library, mapped to
  the right subclass (`CorruptionError`, `TruncatedError`, `EncryptionError`, …)
- [ ] No catch-all: an unrecognized exception returns `None`, so it surfaces in tests
- [ ] A backend that raises outside those helpers still chains (`raise … from exc`) and
  still goes through the translator
- [ ] Every mapped branch is exercised by a corrupt, truncated, encrypted or
  wrong-password fixture
- [ ] `OSError`, `KeyboardInterrupt` and `MemoryError` propagate, except where a spec
  says otherwise

## Hierarchy

`ArchiveyError` (`src/archivey/exceptions.py`) splits by failure domain, not by
format: `OpenError`, `ReadError` (`CorruptionError` → `TruncatedError`,
`EncryptionError`), `ExtractionError`, `ResourceLimitError`,
`UnsupportedFeatureError`, `PackageNotInstalledError`. `ArchiveyUsageError` is the
caller's mistake and stays outside that tree.

- [ ] A new exception type fits a domain; a per-format subclass needs a reason
- [ ] The message does not interpolate a name that is also an attribute
  (`archive_name`, `member_name`, …): `ArchiveyError` escapes the message and renders
  the attributes itself
- [ ] A changed type was checked against what catches it upstream — a `TruncatedError`
  from the wrong layer ends password-candidate iteration

## Logging

The library never installs handlers. Loggers come from `internal/logs.py`; a
hand-typed `logging.getLogger("archivey…")` fails `tests/test_logs.py`.

- [ ] Raise rather than log and continue, unless the API defines a sentinel or a
  diagnostic for that case
- [ ] No passwords, keys or member contents in a log line or a message
