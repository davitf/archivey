# recognize-udif-dmg — name a UDIF disk image and refuse it

**Status:** Ready to implement. Depends on nothing. Blocks nothing. Breaking? no. Effort: small.

**Why it matters:** A compressed `.dmg` starts with a real zlib, bzip2 or xz block. Opening one extracted that block and called the rest of the image trailing data. A backup-disk scan found 138 files opened that way, 111 of them as a successful 512-byte extraction.

**What it does:** Detection looks for the 512-byte `koly` block at the end of a seekable file, or at the start of an old image, and reports the file as a UDIF disk image. Opening it raises an error that names the image. Nothing reads the blocks.

**Decided:** The check is the same 12 bytes 7-Zip uses: `koly`, version 4, header size 512. It runs after the ISO check and before the content probes, so a zlib-first image is not claimed as zlib. A bzip2 or xz header that is the first block loses to the trailer. The `.dmg` name is not evidence. A pipe is not rewound; a long image on a pipe still opens as its first block. `format=` naming a compressor still reads that stream. There is nothing to install.

**Your call later:** None — the design is settled. Reading the image is a separate feature.

**Bottom line:** The image is named and refused. A real compressor stream is unchanged.
