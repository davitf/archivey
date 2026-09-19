#!/usr/bin/env python3
"""Decide whether the automated review loop should run, and on which pull request.

Stdlib only, no GitHub access: the workflow collects the facts, this script makes
the decision. That split is the point — the decision is the part worth testing, and
a gate that has to be exercised by pushing to a pull request is a gate nobody tests.

Reads one JSON object on stdin, writes one JSON object on stdout:

    {"run": true, "pr": 365, "round": 2, "head_sha": "abc…", "reason": "...",
     "cap_reached": false, "forced": false, "enrol": false, "final": false}

Two shapes go in. A single event (`pull_request`, `issue_comment`,
`workflow_dispatch`) carries one pull request's facts at the top level. The
`schedule` shape carries `candidates`, every pull request currently in the loop,
and `now`; the answer names which one to review, or none.

Round state lives in ``loop:round-N`` labels on the pull request rather than in this
script: the workflow that runs round N applies the label, so the next event can read
the count back. Labels are also the only piece of loop state a human can see and
change from the GitHub UI, which is what makes the loop stoppable without a commit.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone

#: How many automated review rounds run before the loop hands the PR back to a human.
MAX_ROUNDS = 3

#: How long a pull request's head commit must sit untouched before a round starts.
#:
#: This is the whole reason the loop is scheduled rather than push-driven. An agent
#: implementing a ticket pushes several times in a few minutes — on the first pull
#: request the loop saw, four commits landed inside thirteen minutes — and a round per
#: push would spend the cap reviewing half-written work and then have nothing left for
#: the finished branch. Silence is the only "the implementer has stopped" signal
#: GitHub offers, so the loop waits for it.
QUIET_MINUTES = 10

#: Opt out entirely. Wins over everything, including an explicit ``@claude review``.
LABEL_OFF = "loop:off"
#: In the loop. Applied when a PR enrols, and the only thing the scan looks for.
LABEL_ON = "loop:on"
#: The loop finished: clean review, or the round cap is spent.
LABEL_DONE = "loop:done"
#: Parked on a maintainer decision. Cleared by whoever answers it.
LABEL_DECISION = "loop:decision"
#: Parked by a human for any other reason.
LABEL_HOLD = "loop:hold"

ROUND_LABEL = re.compile(r"^loop:round-(\d+)$")

#: Branch prefix Cursor's cloud agents use, which is what auto-enrols a new PR.
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

#: Labels that park the loop. A pull request carrying one is skipped by the scan.
PARKED_LABELS = (LABEL_DONE, LABEL_DECISION, LABEL_HOLD)


@dataclass(frozen=True)
class Decision:
    run: bool
    round: int
    reason: str
    pr: int = 0
    head_sha: str = ""
    cap_reached: bool = False
    forced: bool = False
    #: Add `loop:on`: this pull request belongs in the loop and was not in it yet.
    enrol: bool = False
    #: The round about to run is the last automatic one, so say so when it finishes.
    final: bool = False


def current_round(labels: list[str]) -> int:
    """Highest ``loop:round-N`` on the PR, or 0 if the loop has not run yet."""
    rounds = [int(m.group(1)) for label in labels if (m := ROUND_LABEL.match(label))]
    return max(rounds, default=0)


def parse_time(value: object) -> datetime | None:
    """A GitHub timestamp (`2026-09-19T05:40:26Z`) as an aware datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def enrolled(labels: list[str]) -> bool:
    """Is this pull request in the loop?

    A branch name enrols a *new* pull request, once, when it opens. Past that point
    only the label counts. Reading the prefix here instead would sweep in every
    `cursor/*` pull request that was open before the loop existed — eight were open
    the day it landed, and a loop that reviewed all of them would have been switched
    off within the hour.
    """
    return LABEL_ON in labels or current_round(labels) > 0


def decide(event: dict) -> Decision:
    """The answer for one event: which pull request to review, and as which round."""
    if event.get("event_name") == "schedule":
        return _choose(event)
    return replace(
        _classify(event),
        pr=int(event.get("number") or 0),
        head_sha=str(event.get("head_sha") or ""),
    )


# --- single events ------------------------------------------------------------------


def _classify(event: dict) -> Decision:
    labels = list(event.get("labels") or [])
    done = current_round(labels)
    nxt = done + 1
    final = nxt >= MAX_ROUNDS

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
        # A round bought by hand is never the "final" one: whoever asked for it can
        # ask again, so the loop has no business announcing that it is finished.
        return Decision(True, nxt, "requested by comment", forced=True)

    if event_name == "workflow_dispatch":
        if event.get("force"):
            return Decision(True, nxt, "requested manually", forced=True)
        if nxt > MAX_ROUNDS:
            return Decision(False, done, "round cap spent", cap_reached=True)
        return Decision(True, nxt, "requested manually", final=final)

    if event_name != "pull_request":
        return Decision(False, done, f"event {event_name!r} does not drive the loop")

    if event.get("cross_repository"):
        # A fork's pull_request run has no secrets, so the review would fail anyway.
        return Decision(False, done, "pull request is from a fork")

    action = event.get("action", "")
    if action not in ("opened", "ready_for_review"):
        # `synchronize` deliberately does not appear here. A push is how work in
        # progress looks, not how finished work looks; the scheduled scan decides.
        return Decision(False, done, f"action {action!r} does not drive the loop")

    head_ref = str(event.get("head_ref") or "")
    if not (head_ref.startswith(CURSOR_BRANCH_PREFIX) or enrolled(labels)):
        return Decision(False, done, "pull request is not enrolled in the loop")

    if action == "opened":
        # Nothing to review yet — an agent opens the pull request early and keeps
        # pushing. Enrol it and let the scan pick it up once it goes quiet.
        return Decision(
            False, done, "enrolled; the scan reviews it once it is quiet", enrol=True
        )

    for label in PARKED_LABELS:
        if label in labels:
            return Decision(False, done, f"{label} is set", enrol=True)

    if nxt > MAX_ROUNDS:
        return Decision(False, done, "round cap spent", cap_reached=True, enrol=True)

    # "Ready for review" is a person or an agent saying the work is finished, which is
    # exactly the signal the quiet period exists to infer. Do not make them wait for it.
    return Decision(True, nxt, "marked ready for review", enrol=True, final=final)


# --- the scheduled scan --------------------------------------------------------------


def _scheduled(candidate: dict, now: datetime | None, quiet: timedelta) -> Decision:
    labels = list(candidate.get("labels") or [])
    done = current_round(labels)
    nxt = done + 1
    pr = int(candidate.get("number") or 0)
    head_sha = str(candidate.get("head_sha") or "")

    def out(
        run: bool,
        rnd: int,
        reason: str,
        *,
        cap_reached: bool = False,
        final: bool = False,
    ) -> Decision:
        return Decision(
            run,
            rnd,
            reason,
            pr=pr,
            head_sha=head_sha,
            cap_reached=cap_reached,
            final=final,
        )

    if LABEL_OFF in labels:
        return out(False, done, f"{LABEL_OFF} is set")
    if candidate.get("cross_repository"):
        return out(False, done, "pull request is from a fork")
    for label in PARKED_LABELS:
        if label in labels:
            return out(False, done, f"{label} is set")
    if not enrolled(labels):
        return out(False, done, "pull request is not enrolled in the loop")
    if nxt > MAX_ROUNDS:
        return out(False, done, "round cap spent", cap_reached=True)
    if not head_sha:
        return out(False, done, "head commit is unknown")
    if head_sha == str(candidate.get("last_reviewed_sha") or ""):
        return out(False, done, "this commit has already been reviewed")

    pushed = parse_time(candidate.get("head_committed_at"))
    if pushed is None:
        return out(False, done, "head commit has no timestamp")
    if now is not None and now - pushed < quiet:
        return out(
            False, done, f"still being pushed to — quiet for under {QUIET_MINUTES}m"
        )

    return out(
        True,
        nxt,
        f"no new commits for {QUIET_MINUTES} minutes",
        final=nxt >= MAX_ROUNDS,
    )


def _choose(event: dict) -> Decision:
    """One pull request per tick, longest-waiting first.

    One is not a throttle bolted on afterwards: a scan that started a review on
    everything eligible would, the first time it ran, start one per enrolled pull
    request at once. Taking the oldest head commit first is also the order a person
    would pick, and it is total — ties break on the pull request number — so a tick
    that is interrupted and retried makes the same choice.
    """
    now = parse_time(event.get("now"))
    quiet = timedelta(minutes=QUIET_MINUTES)

    ready = []
    for candidate in event.get("candidates") or []:
        decision = _scheduled(candidate, now, quiet)
        pushed = parse_time(candidate.get("head_committed_at"))
        if decision.run and pushed is not None:
            ready.append((pushed, decision.pr, decision))

    if not ready:
        return Decision(False, 0, "no pull request is waiting for a round")
    ready.sort(key=lambda item: (item[0], item[1]))
    return ready[0][2]


def main() -> int:
    event = json.load(sys.stdin)
    json.dump(asdict(decide(event)), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
