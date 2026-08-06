# Reject a conflicting explicit format on a directory path

## Why

`open_archive(path, format=ArchiveFormat.ZIP)` on a path that happens to be a
directory opens it as a directory pseudo-archive. `core.py` overwrites the caller's
argument before it is ever consulted:

```python
resolved_format = format
if isinstance(reader_source, Path) and reader_source.is_dir():
    resolved_format = ArchiveFormat.DIRECTORY
```

No error, no diagnostic. This is the only place an explicit format assertion is
silently overruled — `open_stream` refuses a container format, a password on a format
with no encryption raises `UnsupportedOperationError`, a multi-volume sequence for
anything but 7z/RAR raises.

The way callers reach it is the reason it matters: a variable holding what the caller
believes is an archive path — user input, a config value, a glob result — points at a
directory instead. They passed `format=` precisely because they knew what they
expected. Instead of an error they get a reader over the directory tree, and
everything downstream succeeds on the wrong data.

Raised by the maintainer while reviewing `docs/opening-and-listing.md` (#224), where
documenting the behaviour made it look like a designed feature.

## What changes

`open_archive` raises `ArchiveyUsageError` when `source` resolves to a directory path
and `format=` is neither `None` nor `ArchiveFormat.DIRECTORY`. The message names the
path and the requested format, and gives both ways forward.

`docs/opening-and-listing.md` changes one sentence: the argument is rejected rather
than ignored.

## Impact

- `src/archivey/core.py` — one guard at one call site.
- `tests/test_directory.py` — the conflict raises; explicit `DIRECTORY` still opens.
- `openspec/specs/archive-reading/spec.md` — one requirement, three matrix rows.
- **Behaviour change**, and technically breaking: any caller relying on today's
  behaviour is passing a format that is being ignored, so the only code that breaks is
  code already not doing what it says. Pre-`0.2.0`, so no compatibility promise
  applies yet.
