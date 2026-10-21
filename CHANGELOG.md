# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries are written at release time, not by each pull request: new features, public
API changes and security fixes get a line each, and other bug fixes are summarized in
one line. How to write the entry: see
[`dev-docs/release-checklist.md`](dev-docs/release-checklist.md).

## [Unreleased]

## [0.2.0] - 2026-10-21

First public release. This repository is a rewrite: the earlier v1 / alpha line
(previously published from what is now
[`davitf/archivey-old`](https://github.com/davitf/archivey-old)) is a separate codebase
with no compatibility promise. Treat 0.2.0 as the first release of this library.

The public API is not frozen until 1.0: minor releases before then may still change it,
and each change will be listed here. No major changes are expected.

### Added

- **One interface for reading, streaming and extracting archives.** `open_archive()`
  returns a reader that lists members, reads one member, or streams them all in one pass,
  the same way for every format.
- **Formats:** ZIP, TAR (plain and compressed: gzip, bzip2, xz, zstd, lz4, Unix
  compress), 7z, RAR (1.5 through RAR5), ISO 9660 (Joliet, Rock Ridge, zisofs; needs
  `pycdlib` 1.22 or later), plain directories, and single-file streams (gzip, bzip2, xz,
  lzip, LZMA, zstd, lz4, zlib, Brotli, Unix compress). Self-extracting archives are read
  behind their stub. An ISO whose directory tree loops back on itself, in any of its
  trees, is refused at open.
- **Automatic format detection** from the file's content, with the extension as the last
  signal, so a misnamed file still opens.
- **Native readers.** archivey parses ZIP, TAR, 7z and RAR headers itself, without
  `zipfile`, `tarfile` or any third-party package, so listing them needs no extra
  install unless a TAR's compression is outside the standard library (zstd before Python
  3.14, lz4). 7z data decodes without external tools, BCJ2 included. RAR data needs
  RARLAB `unrar` or `rar`, or `unar`.
- **Encrypted archives:** ZipCrypto and WinZip AES in ZIP, AES in 7z (headers too),
  and RAR3/RAR5 encryption, with one password or a list of candidates.
- **Safe extraction by default.** `extract_all()` blocks path traversal, symlink and
  hardlink escapes, special files and deceptive names, and caps total size, compression
  ratio and entry count. Three policies (`STRICT`, `STANDARD`, `TRUSTED`) set how much
  to allow.
- **Extraction controls.** `overwrite=` decides what happens to a file already in the
  destination (refuse, skip, replace, or rename to `name (N)`), `on_error=` whether one
  failing member stops the rest, and `on_progress=` reports each member. `dry_run=True`
  (`archivey x --dry-run` from the shell) runs the whole extraction into a temporary
  folder that is deleted before the call returns, to show what would be written or
  refused.
- **Resource limits beyond extraction:** caps on listing size, decoder memory,
  password key-derivation work and temporary spool files, all on `ArchiveyConfig`, and
  all raising `ResourceLimitError`.
- **Built for streaming.** `stream_members()` hands out each member as a stream, in
  archive order and in one pass, so a solid 7z or RAR is decoded once and a large member
  is read in chunks rather than held in memory. TAR, ZIP and the single-file formats
  also read from a non-seekable source such as a pipe, and a compressed file from a pipe
  the sender keeps open is decompressed from what has arrived. The documented access
  costs say which operations re-read or re-decompress.
- **Seeking in compressed files.** With `seekable_members=True`, a `.zst` seeks by frame
  (reading the Seekable Zstandard seek table when present) and a gzip file by member or
  BGZF block, instead of decompressing from the start.
- **Crash isolation.** The native decoders seen to crash on hostile input, rapidgzip and
  pyppmd, run in a child process, so a crash costs the member being read instead of your
  process. `ArchiveyConfig.process_isolation` can put every native decoder there, or
  none.
- **Overlapping operations on one reader.** With `concurrent_operations=True`, random
  reads, listing, `stream_members()` passes and `extract_all()` may overlap on one
  reader, from one thread or several, in every format.
- **Verified reads.** A member whose CRC or other checksum fails, or that ends short,
  raises from `read()`, and a `stream_members()` pass goes on to the members after it
  wherever the format lets it reach them. Codec errors map to one `ArchiveyError`
  hierarchy, and non-fatal oddities are reported as structured diagnostics that
  `DiagnosticPolicy.STRICT` turns into errors.
- **Zero-dependency core.** ZIP, TAR, directories and the standard-library codecs need
  no extra packages. `[recommended]` adds the other codecs, crypto and ISO;
  `[seekable]` adds the rapidgzip accelerator; `[free-threaded]` is the GIL-safe set.
  `format_availability()` says what the current install can read and what to add.
- **The `archivey` command:** list, test, extract (with the same safety defaults) and
  inspect archives from the shell, and `cat` a member, or a whole decompressed stream,
  to stdout. `uvx archivey-cli` runs it with the recommended
  extras, through the `archivey-cli` companion package.
- **Path helpers in `archivey.paths`:** glob matchers to pass as `members=`,
  `match_glob()` (`*` stays inside one folder, `**` matches any depth) and
  `match_tar_glob()` (`tar`-style: `*` also matches `/`, and a folder's name selects
  everything in it), and `numbered_name()`, the `name (N)` spelling that
  `OverwritePolicy.RENAME` writes.
- Tested on Python 3.11 to 3.14 on Linux, macOS and Windows, plus free-threaded 3.13t on
  Linux. PyPy 3.11 also runs the test suite but isn't supported yet; the install page
  says what works there.

### Removed

- `crc32_digest`, public in 0.2.0rc1. To compare a CRC-32 from `member.hashes` with
  your own, use `zlib.crc32(data).to_bytes(4, "big")`.

### Performance

archivey's time over the standard library's or reference reader's time (lower is
better), from one benchmark run on 2026-10-21 at commit `de8a6565`. Details, method and
the full table are in [`benchmarks/RESULTS.md`](benchmarks/RESULTS.md).

- Reading: a gzip file 0.9–1.1× (it varies between runs); every member of a `.tar.bz2`
  1.2×, `.tar.gz` 1.7×, ZIP 1.9×, plain TAR 2.7×. Extracting a ZIP: 2.5×.
- Listing: TAR 2.3×, ZIP 8.5×, 7z 1.5–2.3×, RAR 1.9–5.5×.
- ZIP data reads at about the same rate as `zipfile` (about 370 MiB/s); the gap is per
  member, about 6 000 members a second against `zipfile`'s 51 000.

### Security

- [`SECURITY.md`](SECURITY.md) describes private vulnerability reporting through
  [GitHub Security Advisories](https://github.com/davitf/archivey/security/advisories/new).
  The threat model and its open items are in `dev-docs/threat-model.md`.

## [0.2.0rc1] - 2026-10-18

Release candidate for 0.2.0, to test the release flow. 0.2.0 differs from it in three
ways: it adds the `archivey-cli` companion package (an `archivey-cli` 0.2.0rc1 was
published after the tag), it removes `crc32_digest` (see Removed under 0.2.0), and it
fixes bugs.

<!--
What goes in this file, from 0.2.0 on:

- One line per new feature and per public API change (Added / Changed / Deprecated /
  Removed).
- Each security fix gets its own line under Security.
- A Performance section, after Removed and before Security, gives the peer wall ratios
  for the release with the run's date and commit, and links benchmarks/RESULTS.md for
  the host, method and analysis (dev-docs/release-checklist.md, section 2).
- Other bug fixes share one line under Fixed, e.g. "Fixed N bugs in ZIP and RAR
  reading", unless one changes behavior users rely on.
- Pull requests do not edit this file. The release PR writes the entry from the merged
  PRs (dev-docs/release-checklist.md, section 1).

At release, add compare links below this comment.
-->

[Unreleased]: https://github.com/davitf/archivey/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/davitf/archivey/releases/tag/v0.2.0
[0.2.0rc1]: https://github.com/davitf/archivey/releases/tag/v0.2.0rc1
