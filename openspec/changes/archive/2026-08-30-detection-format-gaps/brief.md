# detection-format-gaps — three formats archivey supports but cannot recognise

**Status:** Ready to implement. Depends on nothing. Blocks nothing, but lands first among the detection changes. Not breaking — every case raises a detection error today. Effort: small.

**Why it matters:** Three inputs whose own decoders accept them are rejected by detection. A zstd stream sitting behind a skippable frame, a zlib stream at any window size below thirty-two kilobytes, and an LZMA Alone stream whose dictionary field is zero. All three are legal, all three decompress fine if you force the format, and only detection refuses. On a backup corpus, a file you cannot open is the failure that matters.

**What it does:** Walks past zstd skippable frames by their declared sizes to find the real frame. Replaces zlib's four-entry list of known headers with the grammar the format actually specifies, which admits sixty-six header pairs rather than four — today six of the seven legal window sizes are missed. And removes the rejection of a zero dictionary size in LZMA Alone.

**Decided:** The Alone fix cannot ship alone. That guard is an ordering workaround, and its own comment says so — it also stops a zero-filled ISO system area decoding as an empty Alone stream. Verified by lifting it: a real ISO then detects as LZMA Alone. So this change also moves far magic ahead of the content probes, which is where it belongs anyway, and closes a second live defect on its own — a bootable ISO whose reserved boot area is claimed by the Brotli probe. That reorder was claimed by the prefixed-archive change; it moves here, and that change drops it.

**Your call later:** None. Each fix is measured, and the reorder's regression pin is a zero-system-area ISO fixture.

**Bottom line:** Small, self-contained, and it removes three ways to fail on a valid file.
