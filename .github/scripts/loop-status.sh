#!/usr/bin/env bash
# Maintain one "where this PR stands" comment on a pull request, in plain language.
#
#   loop-status.sh <pr-number> <body-file>
#
# The comment is identified by an HTML marker and edited in place rather than
# appended to, so a PR that goes three rounds has one status comment, not three.
# It is deliberately separate from the review itself: the review is precise and long,
# this is the line you read to know whether anything is waiting on you.
set -euo pipefail

pr="$1"
body_file="$2"
marker="<!-- archivey-review-loop-status -->"

# A relative link does not resolve from an issue comment, so spell the URL out.
doc="https://github.com/$GITHUB_REPOSITORY/blob/main/dev-docs/review-loop.md"

body="$(printf '%s\n\n%s\n\n---\n_Posted by the [review loop](%s). [Claude Code](https://claude.ai/code) wrote the review; this comment is bookkeeping._\n' \
  "$marker" "$(cat "$body_file")" "$doc")"

existing="$(gh api "repos/$GITHUB_REPOSITORY/issues/$pr/comments" --paginate \
  --jq "[.[] | select(.body | startswith(\"$marker\"))] | first | .id // empty")"

if [ -n "$existing" ]; then
  gh api --method PATCH "repos/$GITHUB_REPOSITORY/issues/comments/$existing" \
    -f body="$body" --silent
else
  gh api --method POST "repos/$GITHUB_REPOSITORY/issues/$pr/comments" \
    -f body="$body" --silent
fi
