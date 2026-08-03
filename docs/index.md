# Archivey

Archivey reads, streams, and safely extracts ZIP / TAR / RAR / 7z / ISO / directory /
single-file-compressed archives behind one uniform interface.

```python
import archivey

with archivey.open_archive("photos.zip") as reader:
    for member in reader:
        print(member.name, member.size)
```

## Highlights

- **One interface for every format** — ZIP, TAR (`.tar.gz`/`.bz2`/`.xz`/`.zst`/…), RAR, 7z,
  ISO, plain directories, and single-file streams (gzip, bzip2, xz, zstd, lz4, lzip, zlib,
  brotli, Unix compress) all read the same way.
- **Automatic format detection** from content, not just the file extension.
- **Zero-dependency core** — ZIP/TAR/directory and the stdlib codecs work with no extra
  installs; optional formats and accelerators are opt-in [extras](formats.md).
- **Native 7z and RAR metadata readers** — no `py7zr`/`rarfile` on the read path (RAR
  member *data* still uses the system `unrar`).
- **Safe by default** — extraction blocks path traversal, symlink escapes, and archive
  bombs unless you opt out. See [Safe extraction](safe-extraction.md).
- **Streaming-friendly** — read straight from a pipe in a single forward pass, with
  explicit, predictable [access costs](costs.md) for solid archives and seeking.
- **Consistent handling** of symlinks, timestamps, permissions, passwords, and a single
  [exception hierarchy](usage.md#error-handling).

## User guide

1. **[Philosophy](philosophy.md)** — why Archivey exists and the defaults that follow
2. **[Basic usage](usage.md)** — open, list, stream, extract
3. **[Migrating](migrating.md)** — coming from `zipfile`, `tarfile`, `shutil`, `patool`
4. **[Gotchas](gotchas.md)** — traps worth knowing after the basics (read this next)
5. **[Access costs and pitfalls](costs.md)** — hidden decompression costs and how to avoid them
6. **[Formats and extras](formats.md)** — per-format quirks, required libraries, limitations
7. **[Safe extraction](safe-extraction.md)** — what “safe by default” means in practice
8. **[Platforms and threading](support-matrix.md)** — supported Pythons/OSes and what free-threading claims
9. **[API reference](api.md)** — generated from source
10. **[Acknowledgements](acknowledgements.md)** — libraries, oracles, and design references

## For contributors

This site is the user guide, and nothing else. Contributor material lives in the
[repository](https://github.com/davitf/archivey):

- [`CONTRIBUTING.md`](https://github.com/davitf/archivey/blob/main/CONTRIBUTING.md)
  — coding and testing standards
- [`openspec/specs/`](https://github.com/davitf/archivey/tree/main/openspec/specs)
  — the authoritative behaviour contracts
- [`dev-docs/`](https://github.com/davitf/archivey/tree/main/dev-docs) — decision
  log, threat model, codec analysis, known issues, roadmap, and finished
  investigations
- [`VISION.md`](https://github.com/davitf/archivey/blob/main/VISION.md) — what the
  project is for and how trade-offs get settled
