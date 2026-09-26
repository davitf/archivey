# archive-origin-reporting — say where the archive actually started

**Status:** Ready to implement once `prefixed-archive-detection` lands (it defines the enum this reuses). Blocks nothing. Additive to the public data model. Effort: small–medium.

**Why it matters:** When an archive sits behind an executable stub, archivey opens it correctly and then forgets where it began. The opened archive's metadata says nothing about it, so archivey's own command-line tool detects the format a second time purely to print the offset. Worse, the two ways of opening know different amounts: pass the format explicitly and the parser searches for the payload, finds it, and discards the answer — so the caller who already knew the format ends up with less information than the one who did not. The same file, two doors, two different stories.

**What it does:** The opened archive reports what preceded its payload and where the payload started, the same way on both doors. Where the answer genuinely is not known — a prefixed ZIP opened with an explicit format, where the standard library finds the payload without telling us where it was — it says *not established* rather than claiming byte zero. Underneath, the three formats that can carry a prefix stop resolving their origin three different ways: one shared resolver replaces the two hand-rolled copies, and the RAR version falls out of the same result instead of a second code path.

**Decided:** An enum rather than an is-self-extracting flag, because there are three states and a flag can only hold two — and because a Python zipapp, an executable JAR and a JPEG with a ZIP stuck on the end all start after byte zero and none of them is self-extracting. The enum already exists for the detection result; duplicating the idea with a second, coarser spelling on the opened archive is the inconsistency this repo explicitly guards against.

**Your call later:** Whether a forced-format ZIP should run a tail probe at open time so its origin becomes known too. Deliberately not done here — that would be new reading purely to fill in a metadata field, on the path a caller picked for speed.

**Bottom line:** The offset is already computed on every path; this stops throwing it away and makes both doors tell the same story.
