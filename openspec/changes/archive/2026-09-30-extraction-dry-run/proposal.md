# Dry-run extraction

## Why

A maintainer scanning a pile of old backups wants to know, per archive, what a safe
extraction would do: which members the policy blocks or renames, which are corrupt,
which links cannot be written. Today the only way to find out is to extract for real,
which needs disk for every file's content. A dry run that only calls the checks would
miss the outcomes that depend on the filesystem: a parent resolved through a symlink an
earlier member created, a link that a later member makes escape (O22), collisions, and
hardlink sources. Re-implementing those against an in-memory tree would drift from the
real extraction.

## What changes

`extract_all()` and `extract()` gain `dry_run: bool = False`. With `dry_run=True` the
coordinator runs the same pass into a private scratch directory made with
`tempfile.mkdtemp()`. Directories, symlinks and hardlinks are created for real, so every
check behaves as it does in a real extraction. Each FILE body is read, decompressed,
verified and counted against the limits, then discarded: the file is created empty. The
scratch directory starts empty, so the report is the one an extraction into an empty
destination would return. The caller's destination is refused if a real run would refuse
it (it exists and is not a directory) and is never created. Paths in the report and in
errors are translated to the caller's destination. The scratch directory is removed when
the call returns or raises, whatever modes the archive gave its entries.

`archivey extract --dry-run` uses it: the same per-member lines and exit code, a summary
that says nothing was written, and no single-root hoist.

## Impact

- `safe-extraction`: a new requirement for dry-run extraction.
- `cli`: a new requirement for `extract --dry-run`.
- Code: `ExtractionCoordinator` (`internal/extraction.py`), `extract_all()`,
  `extract()`, `cli/extract_cmd.py`, `cli/main.py`.
- Public API: one keyword argument on `extract_all()` and `extract()`.
