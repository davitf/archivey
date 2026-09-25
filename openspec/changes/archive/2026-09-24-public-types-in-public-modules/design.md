## Context

`archivey/__init__.py` pinned `__module__` to `archivey` on every class and function in
`__all__` defined under `archivey.internal`. A class's source and its string annotations
are both found through `sys.modules[cls.__module__]`, so the pin broke
`inspect.getsource` and needed hints resolved before pinning. A class defined in a public
module needs neither workaround.

## Decisions

**Extraction and registry types go in `archivey/types.py`.** They are pure data. At
runtime they import only `archivey.exceptions` and `archivey.cost`, and neither of those
imports `types` at runtime, so there is no cycle. `MissingComponent`, which
`FormatAvailability` carries, already lived there.

**`FormatInfo` and `DetectionConfidence` get their own module, `archivey/detection.py`.**
`FormatInfo` needs `DiagnosticSummary` at runtime, for its default factory.
`archivey.diagnostics` imports `ExtractionResult` from `types`, so `types` cannot import
`diagnostics` back. A separate module sits above both. The `detection-result-surface`
change decides what else detection exposes. It can add to this module, or leave it as it
is.

**`ArchiveStream` stays pinned.** It is an implementation class on the internal stream
base, and streams are never pickled. The five pinned functions lose nothing to the pin,
because a function carries its own code object.

**Only the display helpers are public, as `archivey.terminal`.** The ruling first
suggested one `cli_helpers` submodule for everything the CLI used from internal code:
the three display helpers and the three enum-spelling helpers. Review showed that only
the display helpers have a use outside archivey. A caller never needs the enum helpers,
because every entry point already converts a string spelling. The CLI used them in two
places, and both are one line without them. The maintainer then ruled: enum helpers
private, the CLI changed to avoid them, and the display module named `terminal`. The
library imports `archivey.terminal` too, since `archivey.exceptions` escapes its
messages with it, which is why the name describes what it does rather than who uses it.

## Risks

A caller who imported `archivey.escaping` or `archivey.internal.extraction_types` breaks.
Neither was documented, and there has been no release.
