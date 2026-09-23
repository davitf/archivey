## MODIFIED Requirements

### Requirement: Source package layout separates public API from implementation

The installable `archivey` package SHALL keep the supported public API at the
package root. Only public API modules appear in `archivey.__all__`: `core.py`,
`types.py`, `exceptions.py`, `cost.py`, and `reader.py`. `archivey.__init__.py`
SHALL re-export the public API so supported callers do not import from
`archivey.internal.*`. Every class and function in `archivey.__all__` that is
defined under `archivey.internal.*` SHALL report `archivey` as its `__module__`,
so `pickle`, `repr()` and `inspect.getmodule` name the public path, and data a
caller persists does not freeze the internal layout. `typing.get_type_hints` SHALL
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
| `pickle.dumps(archivey.OverwritePolicy.SKIP)` | Records module `archivey`, not `archivey.internal.…`; loads back to the same member |
| `typing.get_type_hints(archivey.ExtractionResult)` | Resolves; `Path` is not looked up in `archivey` |
| `inspect.getsource(archivey.OverwritePolicy)` | `OSError`, on every supported Python |
| `import archivey` in a core-only environment | `list_supported_formats()` returns bundled formats without a prior `open_archive()` call |
