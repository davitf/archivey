# Content probes run only for a source named for the format

## Why

LZMA Alone, zlib and Brotli have no magic that detection can trust, so a content probe (a
trial decode of the first bytes) is the only thing that recognises them. On a source with
no name the probe is the only evidence, and real files pass it. A dry-run scan of a
personal backup drive (`dev-docs/investigations/2026-10-backup-scan.md`) detected 57 390
archives, and about 27 300 of them came from the probes: 26 681 git loose objects (real
zlib, but not archives anyone looks for), 437 Brotli hits of which 2 decoded (mostly OLE
files), and 55 LZMA Alone hits of which 4 were real. A sweep of 3 143 libmagic signatures
each followed by zeros found 356 claimed as LZMA Alone at `PROBABLE`, and 315 of those then
read a made-up member of zeros without an error. Each probe fix closes one shape at a time.

A raw stream with none of these magics, and no name, is rare as a file. It is common as
the input of `open_stream`, whose caller already says the source is a compressed stream.

## What changes

- New `ArchiveyConfig.always_probe_content: bool = False`.
- With it `False`, `detect_format` and `open_archive` run only the probe of a stream
  format the source's extension names (`.br`, `.zz`, `.lzma`, their `.tar.` forms, and
  LZMA Alone for `.tlz`). Another name runs no probe; the step is recorded as
  `content_probe` / `NOT_ENABLED_BY_POLICY`. A probe that declines falls through to the
  extension guess, as before.
- With it `True`, every probe runs, as before.
- `open_stream` always runs every probe.
- A member stream (`reader.open()`, `stream_members()`) has `name` set to the member's
  name, as `zipfile`'s `ZipExtFile` does, so a nested `.tar.br` or `.zz` member is still
  found by its name. An `open_stream` result has no name.
- The "nothing matched" `FormatDetectionError` names `format=`, `open_stream()` and
  `always_probe_content=True`.

What a caller loses by default: a nameless raw LZMA Alone, zlib or Brotli source through
`open_archive` or `detect_format` (firmware blobs, git objects, an HTTP body saved with
no name), and a probe-only stream named for another format (zlib bytes named `.xz`),
which now gets that extension's guess and fails to read with
`EXTENSION_FORMAT_UNCONFIRMED`. A matching extension keeps the probe, so a `.zz` holding a
tar still opens as TAR × zlib.

## Impact

- `format-detection`: the detection steps and the content-probe requirement.
- `archive-reading`: the configuration object; member streams' `name`.
- Docs: `docs/formats.md`, `dev-docs/topics/detection.md`, `dev-docs/formats/brotli.md`,
  `dev-docs/formats/single-file.md`.
