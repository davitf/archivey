# detection-evidence-ledger — grade the evidence instead of taking the first hit

**Status:** Ready to implement. Depends on the prefix-workspace change. Blocks the result-surface change and the revised prefixed-archive change. Behaviour-changing, deliberately and visibly. Effort: large.

**Why it matters:** Four defects, all measured. Every magic signature reports certain, so two bytes of gzip magic on a two-byte file is a certain gzip — all fifteen registered signatures do this. A zlib stream of stored blocks, where the decoder literally copied the bytes, reports probable on the strength of that decode. When several detectors accept the same bytes the answer is whichever backend registered first, which is an undocumented intent policy rather than a decision rule. And the unconfirmed-format flag is backwards in both directions: a zero-filled file named backup dot gz fails while reporting the bytes are to blame, when only the filename ever claimed gzip, while a matching extension suppresses the flag on a genuine probe result.

**What it does:** Candidates accumulate typed evidence in seven totally ranked classes, from a complete decode down to a bare filename. Never added up, because the signals are correlated — a name plus a weak decode must never outweigh a checksum. Validators are added so the ranking has something to rank: gzip's header CRC, the XZ flags CRC, the LZ4 header checksum, seven-zip's start-header CRC, RAR's main header, and a real TAR checksum replacing the bare ustar string. An incomplete validation caps a candidate at signature-only. Confidence becomes a projection of the class, not a second score. And ties raise a new ambiguity error rather than resolving by registry order.

**Decided:** All three bounded probes — zlib, LZMA Alone and Brotli — become guess, including a genuine dot br file. Guess now means the bytes did not confirm this, not that it is probably wrong. Filename-only failures start carrying the unconfirmed flag and a matching extension stops suppressing it; measured on two independent trees, extension corroboration caught zero fabrications. Both are deliberate user-visible regressions and need release-note prose.

**Your call later:** What detection reports when an exact payload offset exceeds the index budget, and whether decode budgets are per-detection totals or per-candidate.

**Bottom line:** The centre of the redesign, and the one that changes what existing callers see.
