# sfx-format-detection — detect SFX RAR/7z/ZIP before content probes claim the stub

**Status:** Ready to implement — **raised priority** (Topic 8 MD1 = B, 2026-08-18). Depends on sevenz-sfx-start-offset for seven-zip auto-open behind a stub; RAR and ZIP auto-open can land with this change alone. Blocks Topic 8 guide prose for the formats Detection slash SFX sentence. Not breaking. Effort: medium (includes a short probe-vs-stub investigation).

**Why it matters:** The format-detection spec already requires self-extracting archive detection, but detect_format never scans. The worst failure is silent: a low-entropy executable stub plus a real RAR, seven-zip, or ZIP payload can be claimed by the Brotli content probe (today only 256 bytes, and TruncatedError counts as a hit), and open_archive then returns a fabricated single-file member. ZIP is the most common wild SFX form and already opens under forced format=ZIP. That undercuts the vision rule that behaviour differences are data, never silent guesses — ranked above ordinary docs prose fixes.

**What it does:** Implements the bounded SFX magic scan in detect_format (RAR, 7z, and ZIP local-header needles), sets payload_offset, teaches open_archive to honour that offset, and splits out a requirement that executable-shaped prefixes must not silently become the wrong stream format. The probe policy is not “disable Brotli on MZ” — the implement PR investigates how to tell real Brotli (or peers) from SFX stubs (stronger PE/ELF cues, stricter/larger Brotli probe, scan-first-then-probe, hybrids).

**Decided:** ZIP needle in (PR #253 F1 = A). No-silent-wrong-answer is its own requirement (MD2 = A). One shared `SFX_MAX` (2 MiB) for RAR, detection, and 7z (MD3 = A). Open hands off the offset rather than copying the file. Silent-success regressions mandatory. Maintainer raised priority (MD1 option B).

**Your call later:** Differentiation mechanism — settled as “investigate then pick,” not pre-locked. Land the seven-zip start-offset sibling in the same train so seven-zip SFX auto-open works end to end.

**Bottom line:** High-priority close of a silent wrong-answer gap, with care not to miss real Brotli; implement before rewriting the formats Detection page.
