# format_availability() counts LZ4 for 7z and unrar for RAR

## Why

`format_availability()` said two formats were `FULL` when part of them could not be read:

- 7z reads LZ4 members, which need the `lz4` package, but the registry's list of 7z
  optional codecs left LZ4 out. Without `lz4`, 7z reported `FULL` and an LZ4 member
  raised `PackageNotInstalledError` on read.
- RAR reported `FULL` whether or not a RARLAB `unrar` was on `PATH`. Without one, RAR
  opens and lists and stored members read, but compressed and encrypted members do not.
  That is what `PARTIAL` means.

## What changes

- LZ4 joins the 7z optional codecs. A test compares each container's list with the
  codecs its method table uses, so the list cannot fall behind the table again.
- RAR is `PARTIAL` with `unrar` in `missing` when no usable RARLAB `unrar` or `rar` 6.0
  or later is found. A new `ReadBackend.missing_programs()` hook reports it, and only the
  public `format_availability()` calls it, because finding out runs the binary once to
  read its banner. `open_archive` and `list_supported_formats()` keep their answer
  without the probe: a missing tool never makes a format `NONE`.

Alternatives not taken:

- **Answer for the configured `rar_decompressor`.** `format_availability()` takes no
  config, and adding one is a public API change for little gain: the answer is for the
  default, `AUTO`. A caller who sets `UNAR` or `NONE` knows what they chose.
- **`FULL` when `unar` is usable.** `unar` reads fewer archives than `unrar` (archivey
  refuses the reads it gets wrong), so `PARTIAL` with `unrar` missing is the honest
  answer. It also saves the `unar` RAR5 check at query time.

## Impact

- `backend-registry`: the RAR exemption becomes a counted tool; 7z's optional codec
  list is spelled out; three matrix rows.
- Code: `internal/registry.py`, `internal/base_reader.py`, `backends/rar_reader.py`,
  `backends/rar_unrar.py`.
- Docs: `docs/install.md` §Check what this install can read.
