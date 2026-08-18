# sfx-format-detection — detect SFX RAR/7z before content probes claim the stub

**Status:** Ready to implement — **raised priority** (Topic 8 MD1 = B, 2026-08-18). Depends on sevenz-sfx-start-offset for seven-zip auto-open behind a stub; RAR auto-open can land with this change alone. Blocks Topic 8 guide prose for the formats Detection slash SFX sentence. Not breaking. Effort: medium.

**Why it matters:** The format-detection spec already requires self-extracting archive detection, but detect_format never scans. The worst failure is silent: a low-entropy executable stub plus a real RAR or seven-zip payload can be claimed by the Brotli content probe, and open_archive then returns a fabricated single-file member. That undercuts the vision rule that behaviour differences are data, never silent guesses — ranked above ordinary docs prose fixes.

**What it does:** Implements the bounded SFX magic scan in detect_format, runs it before content probes when the prefix looks like an executable, sets payload_offset, and teaches open_archive to honour that offset so backends read the embedded payload in place.

**Decided:** Scan before probes on MZ or ELF prefixes. Window size aligns with the RAR parser’s two-megabyte SFX limit. Open hands off the offset rather than copying the file. Regression tests must cover the silent-success path, not only FormatDetectionError. Maintainer raised priority (MD1 option B).

**Your call later:** None — the design is settled. Land the seven-zip start-offset sibling in the same train so seven-zip SFX auto-open works end to end.

**Bottom line:** High-priority close of a silent wrong-answer gap; implement before rewriting the formats Detection page.
