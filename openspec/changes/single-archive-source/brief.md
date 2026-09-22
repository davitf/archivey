# single-archive-source — one object carries every guarantee the raw source must give

**Status:** Ready to implement. Builds on the borrowed source streams and bounded header
allocations work, both now on main. Not breaking: no public API changes. Effort is
medium to large, since every backend moves.

**Why it matters:** A caller's file or stream reaches a backend through a stack of
single-purpose wrappers: one so archivey never closes the caller's object, one of two so
a short read cannot break a header parser, and in the ISO backend one more so a size
field in the image cannot force a four gigabyte allocation. Which of them is present
depends on the source, the backend, and whether detection ran, and every backend still
decides for itself whether it opened the source and must close it.

**What it does:** One internal class, ArchiveSource, is what the boundary builds from a
path, a stream or a volume list. It is itself the stream every backend and third-party
parser reads, and it carries full-count reads, ownership, bounded reads and the cheap
facts about the source. The three wrapper classes and the ISO guard go away.

**Decided:** It closes only what archivey opened or built. A path opens its file lazily
and keeps its path, for unrar and volume discovery. Bounding is built into ordinary
reads, since third-party parsers will never call a special method, and only a size that
is a fact clamps a read. Member streams, measurement, and the bound on decoded bytes
stay outside. You decided the replay buffer detection reads from a non-seekable source
moves in as well, so the backend always receives the same object.

**Your call later:** None. The design is settled.

**Bottom line:** A cleanup that makes the source guarantees impossible to miss, and
ready to start now that both changes it builds on are on main.
