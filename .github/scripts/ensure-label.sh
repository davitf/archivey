#!/usr/bin/env bash
# Create a `loop:*` label, or correct one that already exists.
#
#   ensure-label.sh <name> <colour> <description>
#
# Create-only is not enough: a label can be made outside this workflow — adding one
# through the API creates it implicitly, with GitHub's default grey and no description
# — and it would then stay that way forever. The colours are the signal in the pull
# request list (`loop:decision` red, `loop:done` green), so the workflow is the
# definition and it reasserts itself.
#
# It lives in a file rather than in a shell function because two steps need it and a
# function does not survive the step it is defined in. The second caller is the verdict
# step, which applies `loop:round-$ROUND` as its first write under `set -e`: a round
# whose label does not exist ends that step there and loses everything after it — the
# `loop:on` removal, the stale parks, and the status comment. Pre-creating a list of
# labels cannot close that on its own, because a forced `workflow_dispatch` is
# deliberately unbounded and can reach a round no list anticipated.
#
# Never fails the caller. A label this cannot create is one `gh pr edit` will complain
# about in its own right, and losing a round over a description is worse.
set -uo pipefail

gh label create "$1" --repo "$GITHUB_REPOSITORY" \
  --color "$2" --description "$3" 2>/dev/null \
  || gh label edit "$1" --repo "$GITHUB_REPOSITORY" \
       --color "$2" --description "$3" 2>/dev/null \
  || true
