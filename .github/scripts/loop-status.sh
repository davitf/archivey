#!/usr/bin/env bash
# Maintain one "where this PR stands" comment on a pull request, in plain language.
#
#   loop-status.sh <pr-number> <body-file> [reviewed-sha]
#
# The comment is identified by an HTML marker and edited in place rather than
# appended to, so a PR that goes three rounds has one status comment, not three.
# It is deliberately separate from the review itself: the review is precise and long,
# this is the line you read to know whether anything is waiting on you.
#
# The marker also carries the commit the last round read. That is the loop's only
# piece of per-commit state, and it is what stops the scheduled scan from reviewing
# the same head over and over: labels count rounds, this says which commit they were
# spent on. It lives in the marker rather than anywhere else because the comment is
# already the thing the loop must keep up to date, so there is nothing extra to
# forget. Omitting the argument keeps whatever sha the existing comment recorded —
# a status that is not reporting a review ("this is in the loop", "the cap is spent")
# must not silently erase it.
set -euo pipefail

pr="$1"
body_file="$2"
sha="${3:-}"

prefix="<!-- archivey-review-loop-status"

# A relative link does not resolve from an issue comment, so spell the URL out.
doc="https://github.com/$GITHUB_REPOSITORY/blob/main/dev-docs/review-loop.md"

existing="$(gh api "repos/$GITHUB_REPOSITORY/issues/$pr/comments" --paginate \
  --jq "[.[] | select(.body | startswith(\"$prefix\"))] | first // empty")"

if [ -z "$sha" ] && [ -n "$existing" ]; then
  sha="$(jq -r '[.body | capture("sha=(?<s>[0-9a-f]+)").s] | first // ""' <<<"$existing")"
fi

marker="$prefix${sha:+ sha=$sha} -->"

body="$(printf '%s\n\n%s\n\n---\n_Posted by the [review loop](%s). [Claude Code](https://claude.ai/code) wrote the review; this comment is bookkeeping._\n' \
  "$marker" "$(cat "$body_file")" "$doc")"

id=""
if [ -n "$existing" ]; then
  id="$(jq -r '.id' <<<"$existing")"
fi

if [ -n "$id" ]; then
  gh api --method PATCH "repos/$GITHUB_REPOSITORY/issues/comments/$id" \
    -f body="$body" --silent
else
  gh api --method POST "repos/$GITHUB_REPOSITORY/issues/$pr/comments" \
    -f body="$body" --silent
fi
