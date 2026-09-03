# Archivey

Archivey reads, streams, and safely extracts ZIP / TAR / RAR / 7z / ISO / directory /
single-file-compressed archives behind one uniform interface.

```python
import archivey

with archivey.open_archive("photos.zip") as reader:
    for member in reader:
        print(member.name, member.size)
```

## Thirty seconds

```python
import sys

import archivey

# Extract safely. Path traversal, symlink escapes and bombs are blocked by
# default; you opt out, not in.  -> Safe extraction
report = archivey.extract("untrusted.zip", "out/")

# Read one member, verified. A corrupt or truncated member raises from read(),
# never quietly returns short.  -> Reading members
with archivey.open_archive("photos.zip") as reader:
    data = reader.read("subdir/a.txt")

# Stream one member without materialising it.  -> Reading members
with archivey.open_archive("big.tar.gz") as reader:
    for member, stream in reader.stream_members():
        if member.is_file:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                ...

# Read from a pipe: forward-only, single pass. TAR and the single-file compressors only
# — ZIP, ISO, 7z and RAR need a seekable source.  -> Access costs
with archivey.open_archive(sys.stdin.buffer, streaming=True) as reader:
    for member, stream in reader.stream_members():
        ...
```

[Safe extraction](extracting.md) · [Reading members](reading-members.md) ·
[Access costs](access-and-cost.md) · [Install](install.md)

## Highlights

- **One interface for every format** — ZIP, TAR (`.tar.gz`/`.bz2`/`.xz`/`.zst`/…), RAR, 7z,
  ISO, plain directories, and single-file streams (gzip, bzip2, xz, zstd, lz4, lzip, zlib,
  brotli, Unix compress) all read the same way.
- **Automatic format detection** from content, not just the file extension.
- **Zero-dependency core** — ZIP/TAR/directory and the stdlib codecs work with no extra
  installs; optional formats and accelerators are opt-in [extras](install.md).
- **Native 7z and RAR metadata readers** — no `py7zr`/`rarfile` on the read path (RAR
  member *data* still uses the system `unrar`).
- **Safe by default** — extraction blocks path traversal, symlink escapes, and archive
  bombs unless you opt out. See [Safe extraction](extracting.md).
- **Streaming-friendly** — read TAR and the single-file compressors straight from a pipe
  in a single forward pass (formats that keep their index at the end need a seekable
  source), with explicit, predictable [access costs](access-and-cost.md) for solid
  archives and seeking.
- **Consistent handling** of symlinks, timestamps, permissions, passwords, and a single
  [exception hierarchy](errors-and-diagnostics.md).

## User guide

1. **[Install and extras](install.md)** — what to install for the formats you need
2. **[Opening and listing](opening-and-listing.md)** — sources, detection, passwords, what's inside
3. **[Reading members](reading-members.md)** — getting bytes out, and what each outcome means
4. **[Gotchas](gotchas.md)** — traps worth knowing after the basics (read this next)
5. **[Safe extraction](extracting.md)** — what “safe by default” means in practice
6. **[Access costs and pitfalls](access-and-cost.md)** — hidden decompression costs and how to avoid them
7. **[Formats and extras](formats.md)** — per-format quirks, required libraries, limitations
8. **[Errors and diagnostics](errors-and-diagnostics.md)** — what is raised, what is recorded
9. **[Command line](cli.md)** — the `archivey` command
10. **[Migrating](migrating.md)** — coming from `zipfile`, `tarfile`, `shutil`, `patool`
11. **[Platforms and threading](support-matrix.md)** — supported Pythons/OSes and what free-threading claims
12. **[Philosophy](philosophy.md)** — why Archivey exists and the defaults that follow
13. **[API reference](api.md)** — generated from source
14. **[Acknowledgements](acknowledgements.md)** — libraries, oracles, and design references

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
