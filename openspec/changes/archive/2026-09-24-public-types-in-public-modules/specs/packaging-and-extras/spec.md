## MODIFIED Requirements

### Requirement: Source package layout separates public API from implementation

The installable `archivey` package SHALL keep the supported public API at the
package root. Only public API modules appear in `archivey.__all__`: `core.py`,
`types.py`, `detection.py`, `exceptions.py`, `cost.py`, and `reader.py`.
`archivey.__init__.py` SHALL re-export the public API so supported callers do not
import from `archivey.internal.*`.

A public class SHALL be defined in a public module, so its own `__module__` is a
stable public path, `inspect.getsource` works on it, and data a caller persists does
not freeze the internal layout. `ArchiveStream` is the one exception: it is an
implementation class on the internal stream base and is never pickled.

Every class and function in `archivey.__all__` that is still defined under
`archivey.internal.*` SHALL report `archivey` as its `__module__`, so `pickle`,
`repr()` and `inspect.getmodule` name the public path. `typing.get_type_hints` SHALL
still resolve on every such class. `inspect.getsource` on such a class raises
`OSError`: it locates a class through its module and cannot follow the pin, and it
SHALL NOT return lines from another file.

Implementation code SHALL live under `archivey.internal.*` without public
stability guarantees. Format backends SHALL live under
`archivey.internal.backends.*` and register with the registry at import time.
Importing top-level `archivey` SHALL still register all bundled backends. The
codec/stream layer SHALL remain under `archivey.internal.streams.*`. Phase 4
extraction modules SHALL follow the same implementation-under-`internal` rule while
public extraction types and `extract()` live on the public surface.

#### Scenario: package-layout matrix

| Case | Expected |
| --- | --- |
| Application uses documented API (`open_archive`, `ArchiveMember`, etc.) | `import archivey` or public re-exports suffice; no `archivey.internal` import required |
| Caller imports `archivey.internal.backends.zip` or old `archivey.formats.zip_reader` | Not documented, not in `__all__`, and not a stability promise |
| `archivey.OverwritePolicy.__module__`, `archivey.FormatInfo.__module__` | `archivey.types`, `archivey.detection`: the defining public module |
| `pickle.dumps(archivey.OverwritePolicy.SKIP)` | Records `archivey.types`, not `archivey.internal.…`; loads back to the same member |
| `inspect.getsource(archivey.OverwritePolicy)` | Returns the class source |
| A class in `__all__` other than `ArchiveStream` defined under `archivey.internal` | Test failure |
| `archivey.ArchiveStream.__module__` | `archivey` (pinned); `inspect.getsource` raises `OSError` on every supported Python |
| `typing.get_type_hints` on any class in `__all__` | Resolves |
| `import archivey` in a core-only environment | `list_supported_formats()` returns bundled formats without a prior `open_archive()` call |

## ADDED Requirements

### Requirement: Front-end helpers live in the public archivey.cli_helpers submodule

The package SHALL provide a public submodule, `archivey.cli_helpers`, holding the
helpers a command-line front end needs beyond the core API:
`escape_control_chars`, `display_path` and `quoted` for terminal-safe display of
archive-derived text, and `coerce_enum`, `coerce_enum_collection` and
`normalize_spelling` for the enum-argument spellings the library accepts. Names in its
`__all__` SHALL carry the same compatibility promise as `archivey.__all__`. The
submodule SHALL NOT be re-exported from `archivey`, so it does not crowd the root
namespace or the generated API reference for the package root. It SHALL import no
other archivey module at import time, since `archivey.exceptions` imports it.

#### Scenario: cli_helpers surface

| Case | Expected |
| --- | --- |
| `from archivey.cli_helpers import escape_control_chars, coerce_enum` | Works |
| `hasattr(archivey, "escape_control_chars")` / `"cli_helpers" in archivey.__all__` | `False` / `False` |
| `coerce_enum("skip", OverwritePolicy, call=..., param=...)` | `OverwritePolicy.SKIP`, the same coercion the library's entry points apply |
| `coerce_enum("bogus", OverwritePolicy, call=..., param=...)` | `ArchiveyUsageError` naming the accepted spellings |
