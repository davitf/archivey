<!--
The "coffee brief": a spoken-word-friendly summary of this change, readable (or
read aloud) in under a minute. Prose only — NO tables, NO code blocks, minimal
symbols — so text-to-speech reads cleanly. Aim for ~200–280 words. Derive it from
proposal.md / design.md / tasks.md; do not introduce new decisions here.
-->

# extraction-results-authoritative — one channel per fact, and a written ceiling for diagnostics

**Status:** Ready to implement. Breaking, pre-one-point-zero, wants to land before the first tag. Effort: medium.

**Why it matters:** The diagnostics system has a rule saying everything advisory must become a code, and no rule saying what does not qualify. The one attempt at a ceiling, that diagnostics are about the archive rather than about your call, was contradicted by the taxonomy on the day it was written. Three reviewers looked at this independently and all three rejected that cut. They also found a real defect underneath it: extraction is the one operation that returns a structured per-member report, and it reports blocked and failed members twice, once in the report and once as an advisory, with nothing saying which one wins. Worse, a replace-policy collision is genuinely silent in the report today. Both members come back marked extracted at the same path, and only the advisory records which one lost.

**What it does:** Writes the missing ceiling as two clauses, one about admission and one about placement, so a future proposal has a test to fail. Then it applies the placement clause: extraction leaves the advisory channel entirely, four codes are deleted, and every fact moves into the extraction result. A new overwritten status marks the member a later write clobbered, a presented name field records a portability rewrite, and hardlink grouping moves across.

**What it preserves:** Escalation. A new abort-on setting lets a caller still be stopped by a blocked member, a collision, or a rewrite. That also turns an accidental behaviour, which the library's own docstring claims is unimplemented, into a named one. Named strict and pedantic policy presets replace hand-curating twenty-two overrides.
