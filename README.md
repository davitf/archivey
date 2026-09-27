# archivey

[![CI](https://github.com/davitf/archivey/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/davitf/archivey/actions/workflows/ci.yml?query=branch%3Amain)

Python library for reading, streaming, and safely extracting archives (ZIP, TAR, RAR, 7z, ISO, and more) through a unified interface.

This is the **v2** clean-slate implementation. The previous (v1) codebase is archived at
[`davitf/archivey-old`](https://github.com/davitf/archivey-old).

## Features

- **One interface for every format:** ZIP, TAR (plain or compressed with gzip, bzip2, xz,
  zstd, lz4 or Unix compress), 7z, RAR, ISO 9660, plain directories, and single-file
  streams (gzip, bzip2, xz, lzip, LZMA, zstd, lz4, zlib, Brotli, Unix compress).
  List members, read one, or stream them all in one pass, the same way for each.
- **Automatic format detection** from the file's content, so a misnamed file still opens.
- **Safe extraction by default:** path traversal, symlink and hardlink escapes, special
  files and archive bombs are blocked unless you opt out.
- **Resource limits** on listing size, decoder memory, password key-derivation work and
  temporary files, so a hostile archive fails with `ResourceLimitError` instead of
  exhausting the machine.
- **Verified reads:** a member that fails its checksum or ends short raises, never
  returns short data.
- **Streaming from pipes:** TAR and the single-file formats read from a non-seekable
  source in one forward pass.
- **Encrypted archives:** ZipCrypto and WinZip AES in ZIP, AES in 7z, and RAR encryption.
- **Native 7z and RAR metadata readers.** 7z data decodes in-process; RAR data needs
  RARLAB `unrar` or `rar`, or `unar`.
- **Zero-dependency core** for ZIP, TAR, directories and the standard-library codecs,
  plus an `archivey` command for listing, testing and extracting from the shell.
- Python 3.11 to 3.14 on Linux, macOS and Windows.

The API is not frozen until 1.0, but no major changes are expected.

## Install and use

```bash
pip install archivey                 # zero-dependency core
pip install "archivey[recommended]"  # every format and codec that installs everywhere
```

```python
import archivey

# Extract safely: traversal, link escapes and bombs are blocked by default.
archivey.extract("untrusted.zip", "out/")

# List and read members, whatever the format.
with archivey.open_archive("photos.tar.gz") as reader:
    for member, stream in reader.stream_members():
        if member.is_file:
            print(member.name, len(stream.read()))
```

## Documentation

**<https://davitf.github.io/archivey/>**

[Install](https://davitf.github.io/archivey/install/) ·
[Opening and listing](https://davitf.github.io/archivey/opening-and-listing/) ·
[Reading members](https://davitf.github.io/archivey/reading-members/) ·
[Migrating from zipfile/tarfile](https://davitf.github.io/archivey/migrating/) ·
[Formats and extras](https://davitf.github.io/archivey/formats/) ·
[Safe extraction](https://davitf.github.io/archivey/extracting/) ·
[API reference](https://davitf.github.io/archivey/api/)

## Contributing and security

- **[CONTRIBUTING.md](https://github.com/davitf/archivey/blob/main/CONTRIBUTING.md)** — coding / testing standards
- **[VISION.md](https://github.com/davitf/archivey/blob/main/VISION.md)** — priorities and trade-offs; authoritative contracts live in `openspec/specs/`
- **[SECURITY.md](https://github.com/davitf/archivey/blob/main/SECURITY.md)** — private vulnerability reporting
