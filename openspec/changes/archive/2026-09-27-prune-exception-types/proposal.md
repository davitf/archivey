# Prune the public exception types before 0.2.0

## Why

The public tree had 27 classes. Several of them no caller could use: nothing in the
library, the CLI or the documented recipes tells the five `FilterRejectionError`
subclasses apart, `SpoolLimitExceededError` and `ConcurrentAccessError` were caught by
nobody, `UnsupportedFormatError` overlapped `PackageNotInstalledError`, and most of
`UnsupportedOperationError`'s raises were a caller calling a method its chosen mode
forbids, which the maintainer's own rule names a usage error ("calling a method at an
invalid mode"). After the first release, removing a public exception breaks every caller
that names it, while adding one back as a subclass breaks nobody. So this is the cheap
moment. Maintainer ruling, 2026-09-27: all six calls of the exception-hierarchy review
approved.

## What Changes

- **BREAKING (pre-release):** `PathTraversalError`, `SymlinkEscapeError`,
  `SpecialFileError`, `UnportableNameError` and `DeceptiveNameError` are removed; their
  checks raise `FilterRejectionError`, whose message says which check fired.
- **BREAKING:** `SpoolLimitExceededError` is removed; the spool cap raises
  `ResourceLimitError`.
- **BREAKING:** `ConcurrentAccessError` is removed; the second overlapping `open()`
  raises `ArchiveyUsageError` with the same message.
- **BREAKING:** `UnsupportedOperationError` is removed. Random-access and second-pass calls
  on a `streaming=True` reader, and driving a reader from inside a diagnostic callback,
  raise `ArchiveyUsageError`. A RAR password with a line break, a write request, and a
  backend without random access raise `UnsupportedFeatureError`.
- **BREAKING:** `UnsupportedFormatError` is removed. A format whose package is missing
  raises `PackageNotInstalledError`; a format with no backend at all
  (`ArchiveFormat.UNKNOWN`) raises `UnsupportedFeatureError`; `open_stream()` raises
  `ArchiveyUsageError` for an uncompressed `format=` and `FormatDetectionError` when
  detection finds a container.
- `OpenError` and `ReadError` keep their places. Their documented meaning changes to what
  the code already does: a damaged header raises a `ReadError` subclass from
  `open_archive()`, not `OpenError`.

## Impact

- `error-handling`: the hierarchy requirement lists 18 classes; the usage-error
  requirement absorbs the mode cases.
- `safe-extraction`, `archive-reading`, `access-mode-and-cost`, `backend-registry`,
  `diagnostics`, `format-7z`, `format-iso`, `format-rar`, `format-directory`,
  `testing-contract`: scenario tables name the surviving type.
- ADR 0012 gains an amendment; `docs/` and `CHANGELOG.md` follow.
