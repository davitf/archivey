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

**`archivey.cli_helpers` holds both helper groups, the library's own users included.**
The ruling put the helpers in one public submodule. `archivey.exceptions` escapes its
messages with `escape_control_chars`, and `coerce_enum` raises `ArchiveyUsageError`. So
`cli_helpers` imports `archivey.exceptions` inside the function that raises, not at
module level, and the module still imports nothing from archivey at import time.

The name `cli_helpers` is the one the ruling suggested. It is still open. Renaming it
before 0.2.0 is one mechanical commit.

## Risks

A caller who imported `archivey.escaping` or `archivey.internal.extraction_types` breaks.
Neither was documented, and there has been no release.
