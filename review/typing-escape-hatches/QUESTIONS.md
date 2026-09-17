# Questions for the maintainer

One public-signature call. Everything else has a recommended disposition in
[`inventory.md`](inventory.md) and can move in the staged PRs without a
decision.

---

## Q1 — Tighten public `Any` on `ArchiveMember` / `ArchiveInfo` before 0.2.0?

**Question.** `ArchiveMember.extra`, `ArchiveInfo.extra` (`dict[str, Any]`) and
`ArchiveMember.replace(**kwargs: Any)` are public. Substituting `object` is
clean on both checkers. Do we tighten those annotations now, or leave them
until after the freeze?

**Why it matters.** The brief's reason this review exists *now* is freeze
proximity: a public signature tightened after `0.2.0` is a compatibility event
for callers' type checkers. Runtime is unchanged either way. Callers who read
`member.extra["k"]` and treat the value as `Any` (then call a method on it)
would start seeing `object` and have to cast themselves.

**Options.**

- **A — Tighten now** to `dict[str, object]` and `**kwargs: object`. The
  inventory's A26–A28. Lands in staged PR 6, still before freeze if we want it
  in `0.2.0`.
- **B — Leave `Any`.** Record as KEEP-WITH-REASON: format-specific bags are
  intentionally untyped, and `object` is no more useful to a caller who has to
  know the key. Revisit post-`0.2.0`.

**Evidence.** Probe at `94468bd0`: `Any` → `object` on all three sites,
`pyrefly` 0 / `ty` 0. Fields are documented as format-specific extra bags
(`types.py:451`, `:570`, `:536`). `_raw` is explicitly *not* public and is not
part of this question (TIGHTEN to `object` on the private field is PR 2/5).

**Recommendation.** **B.** `dict[str, object]` does not tell a caller what
`extra["iso.namespace"]` is, so it is a checker-purity win with a real
annotation delta for users who already special-case those keys. The freeze
argument cuts the other way only if we had a precise type to offer. We do not.

**Default if you ignore this.** B — public `Any` stays; staged PRs skip A26–A28.
