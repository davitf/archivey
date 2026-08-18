# sevenz-sfx-start-offset — open seven-zip archives whose signature is not at byte zero

**Status:** Ready to implement. Depends on nothing. Unblocks forced format=SEVEN_Z on self-extracting stubs and pairs with sfx-format-detection for auto-open. Not breaking. Effort: medium.

**Why it matters:** Forced format=RAR already opens a self-extracting file because the RAR parser scans for magic. Forced format=SEVEN_Z on the same shape raises CorruptionError — the seven-zip parser always seeks to the start of the file and checks magic at byte zero. The cross-cutting format-detection spec already says native RAR and seven-zip parsers must accept a start offset; format-seven-z never stated it and the reader never implemented it.

**What it does:** Threads a signature origin through the seven-zip parser and reader, checks magic at that origin, and when magic is missing under a forced format, scans forward within a bound matching RAR’s SFX window. Packed-stream and header seeks stay relative to the signature origin. No temporary whole-archive copy.

**Decided:** Explicit start offset plus a forced-format scan for parity with RAR. Spec the obligation in format-seven-z so it lives next to other seven-zip contracts. Fast path when magic is already at the open origin stays unchanged.

**Your call later:** None — the design is settled. Coordinate landing with sfx-format-detection so detection-supplied payload offsets work for seven-zip as well as RAR.

**Bottom line:** Remove the RAR versus seven-zip SFX asymmetry so forced format and, with the sibling change, auto-detect both work for seven-zip self-extracting archives.
