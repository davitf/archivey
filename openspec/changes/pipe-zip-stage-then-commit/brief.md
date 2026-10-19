# pipe-zip-stage-then-commit — stage a pipe ZIP's members, then place them once the directory is read

**Status:** Blocked on a decision. It depends on nothing and blocks nothing. It is breaking for ZIP extraction from a pipe only. Effort: medium.

**Why it matters:** A ZIP read from a pipe learns which members are links, their modes, and which copy of a name is current only at the very end. Today extraction writes every member into the destination as it arrives, then revises it: it turns files into links, takes back members, and moves the caller's own files aside and puts them back. That takes about six hundred lines, and it keeps producing review findings.

**What it does:** During the pass, each selected member's data goes into one private staging folder inside the destination. Once the directory is read, the members are placed by the same code every other extraction uses, each staged file landing by a rename.

**Decided:** The staging folder uses the same temporary-name prefix the docs already call safe to delete. The filter runs once per member, at placement, so a filter that selects by a folder name only the directory gives now works from a pipe as from a file. A run that stops before the directory leaves nothing in the destination, where today it keeps the longer files. Bomb limits count staged bytes when they are written, and give back those of members that are not placed.

**Your call later:** Two questions. First, whether staged output squares with the rule against copying a pipe into seekable storage; the design argues it is output, not a copy of the source. Second, whether it is acceptable that the member count in progress reports moves only at placement, while the byte count still moves during the pass.

**Bottom line:** A simplification with one safety gain, ready to build once those two questions are answered; not urgent.
