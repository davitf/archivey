# Archivey review addendum — moved

The addendum's rules now live in the skill itself (maintainer decision, davitf,
2026-09-24): [`SKILL.md`](../SKILL.md) for what every review needs, and one doc per kind
of review. This page stays so that older links and PR comments citing "addendum §N"
still resolve. Where each old section went:

- **§0** Finding discipline, output shape, verdicts, round budget → `SKILL.md` §2, §3, §4
- **§1–§5** What you are reviewing, "no surprises", coding and contract checks, testing,
  domain checklist → [`code-pr.md`](code-pr.md)
- **§6** Deep reviews → [`deep-reviews.md`](deep-reviews.md)
- **§7** Severity mapping → `SKILL.md` §5
- **§8** Review order, code first then context → [`code-pr.md`](code-pr.md) §Pass 1, §Pass 2
- **§9** Reviewing proposals → [`reviewing-proposals.md`](reviewing-proposals.md)
- **§10** Posting: IDs, headers, attribution, gates, "Measured this round" → `SKILL.md` §6;
  re-reviews and fix-diff scope → [`fix-round.md`](fix-round.md); `SWEPT` markers →
  [`whole-file-sweep.md`](whole-file-sweep.md)
