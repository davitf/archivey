# Address a Linear issue

Follow `.claude/skills/address-linear-issue/SKILL.md`.

Read the Linear issue, implement the fix, open the PR as a draft, and then say you
are finished: take it out of draft. That is what starts the review — a Claude
session running `code-review-skill` posts to the PR within a few minutes. Do not
spawn a reviewer subagent, and do not review your own diff.

The findings come back to the PR. Disposition them with `/address-review`, which
ends with the same "I have finished" signal so the next round starts.
