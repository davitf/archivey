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

### Writing archives
```python
from archivey import open_archive_writer, MemberType

with open_archive_writer("example.zip") as writer:
    with writer.open("file.txt") as f:
        f.write(b"hello")
    writer.add("folder/", MemberType.DIR)
    writer.add("link.txt", MemberType.SYMLINK, link_target="file.txt")
```

