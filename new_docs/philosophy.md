# Philosophy

## What archivey is for

Archivey reads archives. It opens ZIP, TAR (plain or compressed), 7z, RAR and ISO files, as well
as single compressed files such as `.gz` or `.xz`, all through one interface. Code that lists or
reads a ZIP works unchanged on a 7z. It's built for working with an archive's contents from your
code: you can list the members, read one into memory, or go through all of them in one pass,
without writing anything to disk first. It can also extract to disk, with checks that stop a
hostile archive from writing outside the folder you chose. It doesn't write archives yet.

Each member carries every field its format records: times, permissions, owner, link targets,
comments, stored checksums, and the format's own extras. When formats record the same thing,
archivey puts it in the same field. When they record it differently, the difference stays
visible. A TAR modification time is in UTC, so `modified` carries a time zone. A ZIP that has
only the old DOS time stores local time with no zone, so its `modified` has none. Permissions are
set only when the format stores them, and otherwise `mode` is `None`, rather than a guess.
