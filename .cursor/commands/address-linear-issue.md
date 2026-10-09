# Address a Linear issue

Follow `.claude/skills/address-linear-issue/SKILL.md`.

Read the Linear issue, implement the fix, open the PR, and add the `review` label as
the last action after your final push. Adding the label is what starts the review — a
Claude session running `code-review-skill` posts to the PR
(`dev-docs/review-loop.md`). Do not spawn a reviewer subagent, and do not review your
own diff.

The findings come back to the PR. Disposition them with `/address-review`, then add the
`review` label again if the round's closing comment asked to see the fixes.
