# Address review findings

Follow
[`.claude/skills/address-review-findings/SKILL.md`](../../.claude/skills/address-review-findings/SKILL.md)
— read it first. This file is the Cursor entrypoint, not a second copy of the rules.

Scope: the open PR for the current branch, including its CI failures (a red check is a
finding). Give **every** finding an explicit disposition. Do not rewrite unrelated code,
and do not close a finding by deleting the test that caught it.
