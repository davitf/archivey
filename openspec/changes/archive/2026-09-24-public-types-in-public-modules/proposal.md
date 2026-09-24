## Why

Twelve public classes were defined under `archivey.internal` and re-exported, with their
`__module__` pinned to `archivey` so pickled data would not name an internal path. The pin
cost `inspect.getsource` on those classes and needed a type-hint workaround. The
maintainer asked on the pin's PR whether the classes could live where they are declared
instead. Separately, the CLI imported two internal helper modules, and the review-hub
ruling S25-K8 put those helpers in a public submodule, with a rule that the CLI uses only
public API.

## What Changes

- The extraction types, `FormatSupport` and `FormatAvailability` are defined in
  `archivey/types.py`. `FormatInfo` and `DetectionConfidence` are defined in a new public
  module, `archivey/detection.py`. `archivey` still re-exports every name, so imports and
  pickles that name `archivey` keep working. `inspect.getsource` works on them again.
- The pin stays as the safety net, and now covers only `ArchiveStream` and five
  functions. A test fails on any other public class defined under `internal`.
- New public submodule `archivey.cli_helpers`, not re-exported from `archivey`:
  `escape_control_chars`, `display_path`, `quoted` (from the former `archivey/escaping.py`)
  and `coerce_enum`, `coerce_enum_collection`, `normalize_spelling` (from the former
  `archivey/internal/enum_args.py`). Both old modules are removed.
- The CLI imports nothing from `archivey.internal`. A CONTRIBUTING rule says so, and a
  test enforces it.

## Capabilities

### New Capabilities

### Modified Capabilities

- `packaging-and-extras`: public classes defined in public modules; the pin narrows; the
  `archivey.cli_helpers` submodule
- `cli`: the CLI uses only public API
- `error-handling`: the escaping helpers are named at their new path
