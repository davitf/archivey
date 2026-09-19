#!/usr/bin/env python3
"""Decide whether the automated review loop should run, and for which round.

Stdlib only, no GitHub access: the workflow collects the facts, this script makes
the decision. That split is the point — the decision is the part worth testing, and
a gate that has to be exercised by pushing to a pull request is a gate nobody tests.

Reads one JSON object on stdin, writes one JSON object on stdout:

    {"run": true, "round": 2, "reason": "...", "cap_reached": false, "forced": false}

Round state lives in ``loop:round-N`` labels on the pull request rather than in this
script: the workflow that runs round N applies the label, so the next event can read
the count back. Labels are also the only piece of loop state a human can see and
change from the GitHub UI, which is what makes the loop stoppable without a commit.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass

#: How many automated review rounds run before the loop hands the PR back to a human.
MAX_ROUNDS = 3

#: Opt out entirely. Wins over everything, including an explicit ``@claude review``.
LABEL_OFF = "loop:off"
#: Opt in a PR whose branch name does not already enrol it.
LABEL_ON = "loop:on"
#: The loop finished: clean review, or the round cap is spent.
LABEL_DONE = "loop:done"
#: Parked on a maintainer decision. Cleared by whoever answers it.
LABEL_DECISION = "loop:decision"
#: Parked by a human for any other reason.
LABEL_HOLD = "loop:hold"

ROUND_LABEL = re.compile(r"^loop:round-(\d+)$")

#: Branch prefix Cursor's cloud agents use, which is what auto-enrols a PR.
CURSOR_BRANCH_PREFIX = "cursor/"

#: What a human comments to force another round.
#:
#: This is a plain substring, matched case-insensitively, because it has to agree
#: exactly with the `contains(github.event.comment.body, '@claude review')` guard in
#: `.github/workflows/claude.yml` — GitHub expressions have no regex, and `contains`
#: is case-insensitive. The two workflows both listen to `issue_comment`, so a phrase
#: either of them matches loosely is a phrase they would both act on.
COMMENT_TRIGGER = re.compile(r"@claude review", re.IGNORECASE)

#: Who may force a round by commenting.
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

#: Labels that park the loop. A push while one of these is on the PR does nothing.
PARKED_LABELS = (LABEL_DONE, LABEL_DECISION, LABEL_HOLD)


@dataclass(frozen=True)
class Decision:
    run: bool
    round: int
    reason: str
    cap_reached: bool = False
    forced: bool = False


def current_round(labels: list[str]) -> int:
    """Highest ``loop:round-N`` on the PR, or 0 if the loop has not run yet."""
    rounds = [int(m.group(1)) for label in labels if (m := ROUND_LABEL.match(label))]
    return max(rounds, default=0)


def decide(event: dict) -> Decision:
    labels = list(event.get("labels") or [])
    done = current_round(labels)
    nxt = done + 1

    if LABEL_OFF in labels:
        return Decision(False, done, f"{LABEL_OFF} is set")

    event_name = event.get("event_name", "")

    # A human (or workflow_dispatch) forcing a round bypasses the cap and the parked
    # labels. This is the escape hatch for "I answered the decision, carry on" and for
    # a fourth round when the third nearly got there.
    if event_name == "issue_comment":
        if not event.get("is_pull_request"):
            return Decision(False, done, "comment is not on a pull request")
        if not COMMENT_TRIGGER.search(event.get("comment_body") or ""):
            return Decision(False, done, "comment does not ask for a review")
        if event.get("comment_author_association") not in TRUSTED_ASSOCIATIONS:
            return Decision(False, done, "commenter is not a repository collaborator")
        return Decision(True, nxt, "requested by comment", forced=True)

    if event_name == "workflow_dispatch":
        if not event.get("force") and nxt > MAX_ROUNDS:
            return Decision(False, done, "round cap spent", cap_reached=True)
        return Decision(
            True, nxt, "requested manually", forced=bool(event.get("force"))
        )

    if event_name != "pull_request":
        return Decision(False, done, f"event {event_name!r} does not drive the loop")

    # Everything below is an automatic trigger, so every guard applies.
    if event.get("cross_repository"):
        # A fork's pull_request run has no secrets, so the review would fail anyway.
        return Decision(False, done, "pull request is from a fork")

    action = event.get("action", "")

    if event.get("draft") and action != "ready_for_review":
        return Decision(False, done, "pull request is a draft")

    for label in PARKED_LABELS:
        if label in labels:
            return Decision(False, done, f"{label} is set")

    if action in ("opened", "ready_for_review"):
        head_ref = event.get("head_ref") or ""
        enrolled = head_ref.startswith(CURSOR_BRANCH_PREFIX) or LABEL_ON in labels
        if not enrolled:
            return Decision(False, done, "pull request is not enrolled in the loop")
        # Enrolment happens once, when the PR appears. A PR that was open before the
        # loop existed never enrols on its own — it has no round label, so the
        # `synchronize` branch below refuses it until someone adds `loop:on`.
        return Decision(True, nxt, "enrolled at open")

    if action == "synchronize":
        if done == 0 and LABEL_ON not in labels:
            return Decision(False, done, "pull request is not enrolled in the loop")
        if nxt > MAX_ROUNDS:
            return Decision(False, done, "round cap spent", cap_reached=True)
        return Decision(True, nxt, "new commits pushed")

    return Decision(False, done, f"action {action!r} does not drive the loop")


def main() -> int:
    event = json.load(sys.stdin)
    json.dump(asdict(decide(event)), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
