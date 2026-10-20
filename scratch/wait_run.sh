#!/bin/sh
# Wait for a workflow run to complete, then print its job ids and conclusions.
run=$1
while [ "$(gh api "repos/davitf/archivey/actions/runs/$run" --jq .status)" != "completed" ]; do
  sleep 60
done
gh api "repos/davitf/archivey/actions/runs/$run/jobs" --jq '.jobs[] | "\(.id) \(.conclusion)"'
