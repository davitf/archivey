# Archivey

Archivey is a library for reading the contents of many common archive formats. It provides a simple, unified interface on top of several builtin modules and external packages, and improves on some of their shortcomings.


## Features

- Support for ZIP, TAR (including compressed tar variants), RAR, 7z and ISO files, and single-file compressed formats
- Optimized streaming access reading of archive members
- Consistent handling of symlinks, file times, permissions, and passwords
- Consistent exception hierarchy
- Automatic file format detection

## Installation

Recommended:
```
pip install archivey[optional]
```
Or, if you don't want to add all dependencies to your project, add only the ones you need.

RAR support relies on the `unrar` tool, which you'll need to install separately.

### Optional dependencies

Archivey relies on a few extra Python packages (and sometimes external tools) to
handle less common formats. Install them all with `archivey[optional]` or choose
only the ones you need.

| Format(s) | Python package | External tool | Notes |
|-----------|----------------|---------------|-------|
| ZIP | built‑in `zipfile` | – | AES encrypted ZIP files are not supported |
| TAR, `*.tar.gz`, `*.tgz`, `*.tar.bz2`, `*.tar.xz`, … | built‑in `tarfile`<br>Optional: `rapidgzip`, `indexed_bzip2`, `python-xz` | – | Cannot handle encrypted tar archives |
| RAR | `rarfile` | `unrar` | For archives with encrypted headers, `cryptography` is also required |
| 7z | `py7zr` | – | Limited to compression methods supported by py7zr |
| ISO | `pycdlib` | – | ISO encryption is not supported |
| `*.gz`, `*.bz2`, `*.xz`, `*.zst`, `*.lz4` | `zstandard` (for `.zst`)<br>`lz4` (for `.lz4`) | – | Set `use_rapidgzip`, `use_indexed_bzip2` or `use_python_xz` in `ArchiveyConfig` for faster reading |

## Usage

### Streaming access
```python
from archivey import open_archive

with open_archive("example.zip") as archive:
    for member, stream in archive.iter_members_with_io():
        print(member.filename, member.file_size)
        if stream:
            data = stream.read()
```

### Random access
```python
from archivey import open_archive

with open_archive("example.zip") as archive:
    members = archive.get_members()
    # Read the contents of the last file in the archive
    member_to_read = members[-1]
    if member_to_read.is_file:
        stream = archive.open(member_to_read)
        data = stream.read()
```

### Command-line usage

Archivey also provides a small CLI. Pass one or more archives to list or
extract their contents:

```bash
$ archivey example.zip other.tar.gz
```

Use `-x` to extract, `-t` to verify checksums or `-l` just to list. Extra options
like `--use-rar-stream` map to :class:`ArchiveyConfig` flags.

### Configuration

`ArchiveyConfig` toggles optional backends such as `use_rar_stream` for the RAR
stream reader or `use_rapidgzip`/`use_indexed_bzip2`/`use_python_xz` for faster
compressed stream handling. You can pass a config instance to `open_archive` or
set global defaults with `archivey.config.set_default_config`.

