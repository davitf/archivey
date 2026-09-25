## MODIFIED Requirements

### Requirement: Source package layout separates public API from implementation

The installable `archivey` package SHALL keep the supported public API at the
package root. Only public API modules appear in `archivey.__all__`: `core.py`,
`types.py`, `detection.py`, `config.py`, `diagnostics.py`, `exceptions.py`, `cost.py`,
`measurement.py`, and `reader.py`.
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

### Requirement: Terminal-safe display helpers live in the public archivey.terminal module

The package SHALL provide a public module, `archivey.terminal`, holding
`escape_control_chars`, `display_path` and `quoted`: the helpers for showing
archive-derived text to a person without letting it control the terminal. Names in its
`__all__` SHALL carry the same compatibility promise as `archivey.__all__`. The module
SHALL NOT be re-exported from `archivey`, so it does not crowd the root namespace or the
generated API reference for the package root. It SHALL import no other archivey module,
since `archivey.exceptions` imports it.

The enum-argument coercion the library's entry points apply
(`archivey.internal.enum_args`) is not part of this module and not public: a caller
passes the string spelling to the entry point, which converts it.

#### Scenario: terminal surface

| Case | Expected |
| --- | --- |
| `from archivey.terminal import escape_control_chars, display_path, quoted` | Works |
| `hasattr(archivey, "escape_control_chars")` / `"terminal" in archivey.__all__` | `False` / `False` |
| `escape_control_chars("ev\x1b[2Kil")` | `ev\x1b[2Kil` with the escape as four literal characters |
