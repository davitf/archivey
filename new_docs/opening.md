# Opening an archive

## What you can open

`open_archive` takes a path, a binary file object or a folder. A file object is read from its
current position, so an archive that starts partway through a larger file opens as is. A folder
opens like an archive of the files inside it. For an archive split into several files, pass them
as a list, in order.
