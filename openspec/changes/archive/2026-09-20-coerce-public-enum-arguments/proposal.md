# Enum arguments take their string spellings, converted at the boundary

## Why

Nothing converted or checked the enum-typed arguments to the public API, and the code
that consumes them tests membership with `is`. A string is never any member, so an
unrecognised value was not refused — it silently took the other branch. Two of those
branches are the unsafe one:

| Call | Before |
| --- | --- |
| `extract(src, dest, overwrite="skip")` | fell through to REPLACE and **deleted the local file** the caller asked to keep |
| `extract(src, dest, on_error="stop")` | behaved as CONTINUE and **swallowed the corruption error** |
| `extract(src, dest, policy="strict")` | bare `KeyError: 'strict'` out of a transform table |
| `ArchiveyConfig(use_rapidgzip="on")` | stored the string; `AcceleratorMode` is a plain `Enum`, so it read as AUTO |
| `detect_format(src, budget="fast")` | returned unchanged, then `AttributeError` on a budget field several frames down |
| `abort_on=["blocked_member"]` | worked, by accident of `AbortOn` mixing in `str` |

Six arguments, six behaviours, and the two that matter most lose data quietly. The
`format=` arguments were the one group already checked — by *refusing* a string
(`2026-08-17-reject-wrong-typed-format-arguments`).

The maintainer ruled on the general question: **coerce, for CLI and quick-script
friendliness**, and coerce *immediately*, raising on an invalid value rather than
letting it travel. That supersedes the refusal half of the earlier ruling for `format=`
while keeping everything else it decided — a `StreamFormat` object handed to an
`ArchiveFormat` parameter is still refused, with the same message naming the pairs.

## What Changes

- **One conversion helper** (`internal/enum_args.py`) called from every public entry
  point that declares an enum parameter, converting before the first branch on it:
  `extract()`, `extract_all()`, `ArchiveyConfig`, `detect_format(budget=)`.
- **The accepted spellings** are the member's `value`, its name, any case, and `-` and
  `_` interchangeably — so `--abort-on blocked-member` from the CLI's own `--help`
  pastes into a script. An unrecognised spelling raises `ArchiveyUsageError`, outside
  `ArchiveyError` (ADR 0012), naming the spellings that would have worked.
- **`format_args` converts instead of refusing.** An `ArchiveFormat` has no `value`, so
  its spellings are the file extension (`"tar.gz"`) and the attribute name (`"TAR_GZ"`),
  both read off the existing tables so a new format is spellable the day it is declared.
- **The CLI drops its hand-rolled conversion** (`ExtractionPolicy(policy)` plus a
  `.replace("-", "_")` for `AbortOn`) and calls the shared helper, so the library and
  the CLI accept one vocabulary rather than two that drift.
- **A bare string is not a collection of one**: `abort_on="blocked_member"` is refused
  with the list spelling, rather than iterated into eleven single-character members.

Not changing: what any member *means*, the defaults, or which values are valid. A
caller passing members sees no difference.

The cost, stated so it is not discovered later: accepting a string makes the enum
**values** public API. `OverwritePolicy.SKIP.value` cannot be renamed afterwards without
breaking callers silently, where renaming the member would not. That is the price of
the CLI-friendliness this buys.

Left open deliberately: hardening the consuming branch chains themselves
(`_apply_overwrite_policy`'s fall-through to REPLACE, the `is OnError.STOP` sites) so a
*fifth* enum member added later cannot inherit the unsafe branch. Boundary conversion
makes the string case unreachable; that one is about future members and is tracked
internally as its own change.

## Impact

- Specs: `error-handling` (the boundary rule, new), `backend-registry` (the `format=`
  contract, which said a string was refused).
- Code: `src/archivey/internal/enum_args.py` (new),
  `src/archivey/internal/format_args.py` (reject → coerce), `core.py`,
  `internal/base_reader.py`, `reader.py`, `config.py`, `internal/detection.py`,
  `internal/registry.py`, `cli/extract_cmd.py`.
- Tests: `tests/test_enum_arguments.py` (new) — red-green for both data-loss bugs, plus
  the per-enum collision guard that makes case- and dash-insensitivity safe to offer;
  `tests/test_format_arguments.py` gains the same guard for format spellings and loses
  the three cases that asserted a string was refused.
- Docs: `docs/extracting.md` states the string spellings once, where the enums are
  introduced.
- Public API: widening only. Every call this now accepts previously raised, lost data,
  or was ignored.
