# Opening an archive

## What you can open

`open_archive` takes a path, a binary file object or a folder. A file object is read from its
current position, so an archive that starts partway through a larger file opens as is. A folder
opens like an archive of the files inside it. For an archive split into several files, pass them
as a list, in order.

## Format

Archivey identifies the format from the file's first bytes, so a ZIP named `backup.bin` still
opens as a ZIP. It falls back to the file name only when the contents don't settle it. To skip
detection, pass `format=`, such as `format="zip"` or `format=archivey.ArchiveFormat.TAR_GZ`. If
the file isn't in the specified format, it fails like a damaged one.
