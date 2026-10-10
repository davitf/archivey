# native-tar-reader — read TAR with our own parser

**Status:** Design. Coding of the parser and sparse stream can start now; the switch waits for PRs 704, 706 and 716 to merge. Moved into 0.2.0 by the maintainer on 2026-10-10. Effort: medium. The backend ends up about the same size; what goes is the workaround layer.

**Why it matters:** The TAR backend spends about a third of its code working around the standard library's `tarfile`, and the open fixes push that toward half, mostly by overriding private functions. Some problems cannot be fixed that way at all: `tarfile` throws away a sparse member's real stored size, so a few hundred bytes of padding can come back as data, and the same small archive lists a different set of members depending on which Python patch release reads it.

**What it does:** archivey reads the 512-byte TAR headers itself, one at a time, in a plain loop. Members are slices of the stream it already reads, and sparse files are expanded by a small stream that fills holes with zeros. Every hook into `tarfile` goes away. Along the way five small things get fixed: seeking past the end of a member works as in every other format, sparse padding is never served, `raw_name` is always the stored bytes, a GNU incremental backup lists its real names instead of names under a directory of digits, and a streaming pass stops keeping two lists of every member.

**Decided:** No public API changes. Out-of-order sparse maps stay refused, as already ruled. Unknown typeflags stay listed as "other", and a damaged header in the middle still ends the listing with an error after the members before it; resyncing past it is salvage, planned separately.

**Bottom line:** A backend of about the same size that bounds memory by how it is built, gives the same answer on every Python version, and stops breaking when upstream patches `tarfile`.
