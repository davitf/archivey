# Remove the top-level `extract()`

## Why

`archivey.extract(source, dest)` was meant as the one-call replacement for
`shutil.unpack_archive`. The alternative it saved is two lines:

```python
with archivey.open_archive(source) as reader:
    reader.extract_all(dest)
```

To stay one call, `extract()` drifted from `extract_all()` in four ways: it had no
`members=` or `filter=`; its report's diagnostics covered detection and open as well as
extraction; it switched to streaming mode on its own for a non-seekable source; and it
repeated every argument check so that errors named `extract()`. Each difference had to
be documented, specified and tested, and readers of the docs kept asking which one to
use. A caller who needs any of the options or diagnostics is better served by holding the
reader anyway. The maintainer decided to remove it before 0.2.0 (ADR 0019).

## What changes

- `archivey.extract` is removed from `archivey.core`, the package root and `__all__`.
  No deprecation shim: nothing has been released with it.
- `safe-extraction`: the "One-Shot Extraction API" requirement is removed; the
  requirements that named both calls now name `extract_all()` only.
- `diagnostics`: the top-level extraction lifetime goes; an extraction report covers its
  `extract_all()` call, and detection and open diagnostics stay on `reader.diagnostics`.
- `error-handling`, `backend-registry`, `archive-reading`, `packaging-and-extras`:
  argument tables and scenarios that used `extract()` now use `open_archive()` or
  `extract_all()`.
- Docs and tests use `open_archive()` + `extract_all()`, with `streaming=True` for a pipe.

## Impact

Public API: one function fewer. The CLI never called it and is unchanged.
