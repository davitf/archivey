# single-file-open-time-validation — make a wrongly-named single-file archive actually fail

**Status:** Ready to implement. Depends on nothing. Blocks nothing. Behaviour-changing on the error path. Effort: small to medium.

**Why it matters:** Two defects, and the second is data loss. First, the eager open-time check in the single-file reader opens and closes a codec stream without reading, and every standard-library codec validates its header on first read — so the check validates nothing. Forty thousand zero bytes named backup dot gz opens cleanly, lists one invented member, and fails only when you read it. Ten codecs out of ten. Second, and not previously recorded anywhere: with the seekable extra installed and seekable members turned on, a corrupt bzip2 member reads as zero bytes with no error at all, where the same file with that flag off raises a corruption error. A capability flag turning a corrupt archive into an empty successful read is exactly the wrong answer for someone verifying a backup.

**What it does:** The open-time probe pulls one byte, which raises properly for nine of the ten codecs. Unix compress needs a minimum-header length floor instead, because its decoder reads an empty input as an empty stream and no amount of reading distinguishes the two. And an accelerator is required to raise whatever the path it replaces would raise.

**Decided:** Both defects ship together, because the second defeats the fix for the first — the new probe read gets an empty result back on the accelerated bzip2 path and concludes all is well. The bzip2 open cost is accepted and stated rather than discovered: about fourteen milliseconds on a one-point-eight megabyte payload, because bzip2 decodes a whole block to yield one byte.

**Your call later:** Whether the accelerator can report enough to distinguish no-output from a genuine empty stream, or whether the length floor is the only reliable test. Either satisfies the requirement.

**Bottom line:** One confirmed open issue closed and one silent-corruption path found while measuring the fix.
