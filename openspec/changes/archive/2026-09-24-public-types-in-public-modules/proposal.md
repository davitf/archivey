## Why

Twelve public classes were defined under `archivey.internal` and re-exported, with their
`__module__` pinned to `archivey` so pickled data would not name an internal path. The pin
cost `inspect.getsource` on those classes and needed a type-hint workaround. The
maintainer asked on the pin's PR whether the classes could live where they are declared
instead. Separately, the CLI imported two internal helper modules, and the review-hub
ruling S25-K8 asked for a public submodule for what the CLI needs, with a rule that the CLI
uses only public API.

## What Changes

- The extraction types, `FormatSupport` and `FormatAvailability` are defined in
  `archivey/types.py`. `FormatInfo` and `DetectionConfidence` are defined in a new public
  module, `archivey/detection.py`. `archivey` still re-exports every name, so imports and
  pickles that name `archivey` keep working. `inspect.getsource` works on them again.
- The pin stays as the safety net, and now covers only `ArchiveStream` and five
  functions. A test fails on any other public class defined under `internal`.
- `archivey/escaping.py` becomes the public module `archivey.terminal`, not re-exported
  from `archivey`: `escape_control_chars`, `display_path`, `quoted`, with a stability
  promise. The enum-spelling helpers in `archivey/internal/enum_args.py` stay internal.
- The CLI imports nothing from `archivey.internal`. It stops using the enum-spelling
  helpers: argparse has already checked each spelling. A CONTRIBUTING rule says so, and a
  test enforces it.

## Capabilities

### New Capabilities

### Modified Capabilities

- `packaging-and-extras`: public classes defined in public modules; the pin narrows; the
  `archivey.terminal` module
- `cli`: the CLI uses only public API
- `error-handling`: the escaping helpers are named at their new path
