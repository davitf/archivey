#!/usr/bin/env python3
"""Decide whether the automated review loop should run, and on which pull request.

Stdlib only, no GitHub access: the workflow collects the facts, this script makes
the decision. That split is the point — the decision is the part worth testing, and
a gate that has to be exercised by pushing to a pull request is a gate nobody tests.

Reads one JSON object on stdin, writes one JSON object on stdout:

    {"run": true, "pr": 365, "round": 2, "head_sha": "abc…", "head_ref": "…", "reason": "...",
     "cap_reached": false, "forced": false, "enrol": false, "final": false}

Two shapes go in. A single event (`pull_request`, `issue_comment`,
`workflow_dispatch`) carries one pull request's facts at the top level. The
`schedule` shape carries `candidates`, every pull request currently in the loop,
and `now`; the answer names which one to review, or none.

With ``--verdict`` it reads the other end of a round instead: the verdict file the
reviewing agent wrote, answering whether that verdict ends the loop.

    {"verdict": "approved", "stop": true, "summary": "…", "question": "", "reason": "…"}

That is the loop's real stopping rule — the round cap behind it is the backstop, not
the mechanism. See `VERDICT_STOPS`.

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
#:
#: Five rather than three (davitf, 2026-09-21), because the cap had become the only
#: thing that ever stopped the loop. Every pull request the loop has finished carries
#: `loop:round-3`: not one reached a clean review, or any other stop, before the
#: counter ran out. A number doing all the stopping is a number that has to be right,
#: and it is not: on #380 round 3 approved with two nits left, while on #392 and #394
#: round 3 still had real work in front of it and both had to wait for a person.
#:
#: What makes five safe is that the count is no longer the mechanism. A round whose
#: verdict does not ask to see the fix ends the loop where it stands
#: (`VERDICT_STOPS`), so the extra rounds are spent only while the reviewer is still
#: asking for another look. On the two pull requests whose rounds are recorded in
#: full — #380 (nine findings, then three, then two) and #389 (five, three, two) —
#: the verdict rule stops both at round 3 exactly as today, and spends nothing more.
#:
#: This is a ceiling, not a target, and it stays a hard one: an agent cannot raise it
#: (`TRUSTED_BOTS` stops here), and past it a comment buys rounds only up to
#: `MAX_FORCED_ROUNDS`.
MAX_ROUNDS = 5

#: The round past which no *comment* starts anything, however entitled the commenter.
#:
#: `@claude review` from a person is a forced round precisely so it can reach past the
#: cap and past a park. That is right for a person and unbounded for an agent, and the
#: two are indistinguishable here: `address-review-findings` tells the fixing agent to
#: send that phrase after every round, and an agent posting through the maintainer's
#: account (which the review addendum allows) is `OWNER` like the maintainer. Without a
#: ceiling, fix-comment-fix-comment reviews forever at full cost. Three rounds past the
#: automatic cap is enough that a person who genuinely wants another round gets it
#: without noticing this exists. Past it `workflow_dispatch` with `force` is the
#: override, and it stays unbounded because a button in the GitHub UI is not something
#: an agent presses.
#:
#: It was twice the cap while the cap was three, and moving the cap to five kept the
#: spare rounds rather than the multiplier (davitf, 2026-09-21): doubling would have
#: made the worst case an agent can reach ten rounds instead of six, which is the
#: opposite of what raising the cap was asked for. What this number has to be is out of
#: reach of a person buying one more round, and that is a constant, not a ratio.
MAX_FORCED_ROUNDS = MAX_ROUNDS + 3

#: How long a pull request's head commit must sit untouched before a round starts.
#:
#: This is the whole reason the loop is scheduled rather than push-driven. An agent
#: implementing a ticket pushes several times in a few minutes — on the first pull
#: request the loop saw, four commits landed inside thirteen minutes — and a round per
#: push would spend the cap reviewing half-written work and then have nothing left for
#: the finished branch. Silence is the only "the implementer has stopped" signal
#: GitHub offers, so the loop waits for it.
#: Thirty rather than ten (davitf, 2026-09-19). The fallback only has to be *safe*,
#: not fast: an agent that finishes properly says so and gets its round immediately,
#: so the timer is there for the agent that died mid-task. Ten minutes was short
#: enough that an ordinary pause — a long test run, a slow tool call, a session
#: waiting on a person — read as "finished" and spent a round on half-written code.
#: The cost of being wrong is asymmetric: a premature round burns one of the cap's
#: rounds on half-written code, while a late one only delays a branch nobody is
#: watching anyway.
#:
#: **It is measured from the committer's clock, not from the push.** The scan reads
#: `.commit.committer.date`, which is when the commit was made; GitHub does not carry a
#: per-commit push time anywhere the scan can reach. `CONTRIBUTING.md` asks for the test
#: gate before pushing — all three configs when the change can reach them, the everyday
#: leg otherwise — and either takes minutes, so the gap is real: commit at 12:00,
#: run the gate, push at 12:40, and the 12:45 tick sees a head forty-five minutes old
#: and calls a branch quiet five minutes after a push. A rebase that preserves
#: committer dates does the same. Closing it properly means recording when the scan
#: first *saw* a head, which is a second piece of per-commit state; the signal the
#: implementer sends is what makes that not worth carrying yet, since a branch this
#: misjudges is one whose agent did not send it.
QUIET_MINUTES = 30

#: Opt out entirely. Wins over everything, including an explicit ``@claude review``.
LABEL_OFF = "loop:off"
#: How a pull request joins the loop. Removed once a round has run, because
#: ``loop:round-N`` carries the enrolment from then on — the scan matches either.
LABEL_ON = "loop:on"
#: The loop finished: a review that did not ask to see the fix, or the round cap spent.
LABEL_DONE = "loop:done"
#: Parked on a maintainer decision. Cleared by whoever answers it.
LABEL_DECISION = "loop:decision"
#: Parked by a human for any other reason.
LABEL_HOLD = "loop:hold"

ROUND_LABEL = re.compile(r"^loop:round-(\d+)$")

#: Branch prefix Cursor's cloud agents use, which is what auto-enrols a new PR.
CURSOR_BRANCH_PREFIX = "cursor/"

#: What a human comments to force another round: the phrase, at the top of the comment.
#:
#: The position is the whole point. The loop's own prose quotes its trigger — the
#: hand-back comments below say "Comment `@claude review` to buy another round", a
#: review packet puts the phrase in a maintainer question, a dispositions comment
#: quotes it back while explaining what it does. On 2026-09-19 that happened for real:
#: a dispositions comment on #369 spent a forced round reviewing the very pull request
#: that was fixing the loop, and the reviewer's own packet had tripped the same guard
#: nine minutes earlier. Anchoring at the start separates asking for a round from
#: writing about one, because nobody opens a comment with the phrase by accident.
#:
#: The `^` is not redundant with the `.match` below. Anchoring only at the call site
#: put the paperwork-quoting bug one `.match` → `.search` substitution away from coming
#: back, and nothing at the call site would have said so. In the pattern, either method
#: is safe.
#:
#: Leading whitespace is allowed; `\b` keeps "@claude reviewer" out. It stays
#: case-insensitive to match `contains()` in `.github/workflows/claude.yml`, which
#: skips any comment holding this substring anywhere so the assistant and the loop
#: never both answer one comment. That guard being the looser of the two is the safe
#: direction: a comment that merely mentions the phrase now runs neither workflow.
COMMENT_TRIGGER = re.compile(r"^\s*@claude review\b", re.IGNORECASE)

#: Who may force a round by commenting.
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

#: Bots whose ``@claude review`` comment starts a round.
#:
#: A bot cannot be a repository collaborator — GitHub reports `author_association:
#: NONE` for `cursor[bot]` even on a pull request it has been working on — so the test
#: above never reaches it. This is the narrow exception, and it grants strictly less
#: than the human one: a person asking for a round is asking past the cap and past a
#: parked label, because a person is who parked it. An agent saying "I have finished
#: pushing" is not, so the cap and every park still hold for these.
#: Both spellings of each, because the identity an agent posts under is not one thing:
#: Cursor's cloud agent is `cursor[bot]`, a Claude Code session is `claude[bot]`, and
#: `address-review-findings` §7 tells whichever of them holds the branch to send the
#: same phrase. Leaving Claude Code out made the instruction a lie on half its hosts.
TRUSTED_BOTS = frozenset({"cursor[bot]", "cursor", "claude[bot]", "claude"})

#: Labels that park the loop. A pull request carrying one is skipped by the scan.
PARKED_LABELS = (LABEL_DONE, LABEL_DECISION, LABEL_HOLD)

#: What a round's verdict says about whether the loop spends another round.
#:
#: The round cap is a backstop; this is the mechanism. The reviewer already decides the
#: question — `code-review-skill`'s addendum §0 gives it four verdicts, and two of them
#: mean "I do not need to see the result": a conditional approval, whose only open
#: findings are nits with an obvious fix, and a plain comment. Only "request changes"
#: says a 🔴 stands or a fix wants another look. That judgement used to die in the
#: review's prose: the loop read every round that posted anything as "findings" and
#: scheduled the next one, so what stopped it was the counter reaching three.
#:
#: The addendum's own round budget says the same thing from the other side, on measured
#: evidence: in the two weeks to 2026-09-19 every 🔴 in this repository was raised in
#: round 1 or 2, late rounds produced almost only wording findings, and seven findings
#: existed *only* because an earlier fix on the same pull request created them. A loop
#: that keeps going while the reviewer has stopped asking for another look is exactly
#: the back-and-forth that costs credits and finds nothing.
#:
#: A verdict this does not recognise counts as `findings`. A reviewer whose verdict
#: cannot be read has not said it is finished, and the cap still bounds what that costs.
VERDICT_STOPS = {
    # Nothing was found at all.
    "clean": True,
    # Findings were posted and the reviewer does not need to see them fixed: addendum
    # §0's "✅ Approve, conditional on the listed fixes" and "💬 Comment". The findings
    # are still posted in full and the implementer is still asked to fix them — what
    # ends is the re-reading, not the work.
    "approved": True,
    # 🔄 Request Changes. Another round, if the cap has one left.
    "findings": False,
    # A maintainer decision. The loop parks rather than stopping, and the round counter
    # is untouched, so answering it resumes where this round left off.
    "decision": False,
}

#: What an unreadable or unrecognised verdict is treated as. See `VERDICT_STOPS`.
DEFAULT_VERDICT = "findings"


@dataclass(frozen=True)
class Decision:
    run: bool
    round: int
    reason: str
    pr: int = 0
    head_sha: str = ""
    #: The head branch name. Not a decision input — the workflow needs it to tell which
    #: agent holds the branch, and everything the workflow acts on comes off the gate so
    #: there is one place to look when a run does something surprising.
    head_ref: str = ""
    cap_reached: bool = False
    forced: bool = False
    #: Add `loop:on`: this pull request belongs in the loop and was not in it yet.
    enrol: bool = False
    #: The round about to run is the last automatic one, so say so when it finishes.
    final: bool = False
    #: The cap, so the workflow's prose and its label list have one source for it
    #: rather than a copy that drifts the next time the number moves.
    max_rounds: int = MAX_ROUNDS
    #: The highest round any route can reach. The workflow creates a `loop:round-N`
    #: label for each one: the round it is running is the label it then applies, and a
    #: forced round reaches past `max_rounds`.
    max_forced_rounds: int = MAX_FORCED_ROUNDS


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


def _minutes(span: timedelta) -> int:
    """A quiet period in whole minutes, for the reason strings."""
    return int(span.total_seconds() // 60)


def decide(event: dict) -> Decision:
    """The answer for one event: which pull request to review, and as which round."""
    if event.get("event_name") == "schedule":
        return _choose(event)
    return replace(
        _classify(event),
        pr=int(event.get("number") or 0),
        head_sha=str(event.get("head_sha") or ""),
        head_ref=str(event.get("head_ref") or ""),
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
        if not COMMENT_TRIGGER.match(event.get("comment_body") or ""):
            return Decision(False, done, "comment does not ask for a review")
        if event.get("cross_repository"):
            # The `pull_request` path below refuses a fork on the grounds that the run
            # has no secrets and the review would fail anyway. That reason does not
            # hold here: an `issue_comment` run is on the base repository and *does*
            # get them, so this is the one automatic path where a fork round would
            # really start. It is also the only one a fork can reach, since both the
            # others refuse a fork before any label could be written.
            return Decision(False, done, "pull request is from a fork")
        if event.get("comment_author_association") in TRUSTED_ASSOCIATIONS:
            # The ceiling lives inside this branch, not ahead of it. `cap_reached` is
            # not an inert field: the workflow's hand-back step keys on it, adds
            # `loop:done` and rewrites the status comment. Checking it before the trust
            # tests handed those writes to anyone who could type the phrase. The bot
            # path below has its own stop at `MAX_ROUNDS`, and a stranger falls through
            # to the same refusal they get at every other round, so nothing is lost by
            # binding this to the only path that can reach past the ordinary cap.
            if nxt > MAX_FORCED_ROUNDS:
                return Decision(
                    False, done, "forced-round ceiling spent", cap_reached=True
                )
            # `final` still applies. It does not mean "nobody can buy another round" —
            # a person always can, at round 3 as much as at round 5. It means the
            # automatic loop is spent, which is exactly what `loop:done` records, and
            # the workflow clears every stale park as a round runs and re-adds that
            # label only when this is set. Dropping it here took `loop:done` off a
            # pull request that was past the cap and never put it back.
            return Decision(True, nxt, "requested by comment", forced=True, final=final)

        if str(event.get("comment_author_login") or "") in TRUSTED_BOTS:
            # An agent saying it has stopped pushing — the signal the quiet period
            # exists to infer, stated outright, so do not wait for it. Every park
            # still holds: unlike a person, a bot is not who set one.
            for label in PARKED_LABELS:
                if label in labels:
                    return Decision(False, done, f"{label} is set")

            enrol = False
            if not enrolled(labels):
                # This is where the reviewing agent hands an approval back for a pass
                # from zero, and a `claude/*` branch has no enrolment to hand it: only
                # `cursor/*` auto-enrols, deliberately, so that the scan cannot have
                # Claude review its own diff before a second reviewer has looked.
                # Refusing here left that hand-back dead with nothing posted to say so.
                # An explicit request from a trusted agent is a better enrolment signal
                # than a branch prefix, so take it as one.
                #
                # An unenrolled `cursor/*` branch is the opposite case and still
                # refused: Cursor's own pull requests enrol themselves when they open,
                # so one without a label is a pull request that predates the loop, and
                # sweeping those in is what the prefix is kept out of the scan to
                # avoid.
                if str(event.get("head_ref") or "").startswith(CURSOR_BRANCH_PREFIX):
                    return Decision(
                        False, done, "pull request is not enrolled in the loop"
                    )
                enrol = True

            if nxt > MAX_ROUNDS:
                return Decision(False, done, "round cap spent", cap_reached=True)
            return Decision(
                True,
                nxt,
                "the implementing agent says it is finished",
                enrol=enrol,
                final=final,
            )

        return Decision(False, done, "commenter is not a repository collaborator")

    if event_name == "workflow_dispatch":
        if event.get("force"):
            # Same reasoning as the forced comment above: forcing skips the cap, it
            # does not make a post-cap round stop being the last automatic one.
            return Decision(True, nxt, "requested manually", forced=True, final=final)
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
            # No `enrol` here, on purpose. The enrol step is gated on that flag alone,
            # so setting it would run `loop-status.sh` and overwrite the status comment
            # that explains the park — the maintainer's question, on `loop:decision`,
            # or why a round died, on `loop:hold` — with "a review starts by itself".
            # A pull request carrying a park label is already enrolled; that is how it
            # got parked. The cap branch below can afford the flag because the hand-back
            # step runs after the enrol step and overwrites the wrong status with the
            # right one.
            return Decision(False, done, f"{label} is set")

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
            head_ref=str(candidate.get("head_ref") or ""),
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
    if now is None:
        # Fail closed. Not knowing the time means not knowing whether the branch is
        # quiet, and the expensive answer is the one that assumes it is.
        return out(False, done, "scan time is unknown")
    if now - pushed < quiet:
        # `quiet`, not `QUIET_MINUTES`: the threshold is a parameter, the tests pass
        # other values, and a message naming the constant would describe a rule that
        # did not produce it.
        return out(
            False, done, f"still being pushed to — quiet for under {_minutes(quiet)}m"
        )

    return out(
        True,
        nxt,
        f"no new commits for {_minutes(quiet)} minutes",
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


# --- the verdict a round ends with ---------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """What the round's own verdict file says, once it has been made safe to act on."""

    verdict: str
    #: The automatic loop ends here, whatever the round counter still allows.
    stop: bool
    summary: str
    #: Only a `decision` carries one; anything else is dropped rather than shown.
    question: str
    reason: str


def read_verdict(payload: object) -> Verdict:
    """The reviewer's verdict file, normalised.

    The reviewing agent writes this file, so it is the one input to the loop that is
    prose rather than GitHub state, and the failure to guard against is a quiet one: a
    verdict the workflow does not recognise falling through a shell `case` into
    "findings" with nothing saying it happened. Every answer here names what it read.
    """
    if not isinstance(payload, dict):
        return Verdict(
            DEFAULT_VERDICT,
            False,
            "",
            "",
            "the verdict file is not a JSON object",
        )

    raw = payload.get("verdict")
    name = raw.strip().lower() if isinstance(raw, str) else ""
    summary = payload.get("summary")
    question = payload.get("question")

    if name not in VERDICT_STOPS:
        return Verdict(
            DEFAULT_VERDICT,
            False,
            summary if isinstance(summary, str) else "",
            "",
            f"verdict {raw!r} is not one this loop knows; treating it as "
            f"{DEFAULT_VERDICT!r}",
        )

    return Verdict(
        name,
        VERDICT_STOPS[name],
        summary if isinstance(summary, str) else "",
        # A question belongs to a decision. Carrying one on any other verdict would put
        # an unanswerable question in the status comment of a pull request that is not
        # parked, which reads as though something is waiting on the maintainer.
        question if name == "decision" and isinstance(question, str) else "",
        f"verdict {name!r}",
    )


def main() -> int:
    if "--verdict" in sys.argv[1:]:
        try:
            answer = read_verdict(json.load(sys.stdin))
        except json.JSONDecodeError as exc:
            # Not an error exit: the workflow has to act on every round it starts, and
            # a round whose verdict file is unreadable still posted its findings. The
            # parser's own complaint is the reason, because "it is not JSON" on its own
            # sends whoever reads the run to the wrong place.
            answer = Verdict(
                DEFAULT_VERDICT, False, "", "", f"unreadable verdict: {exc}"
            )
        json.dump(asdict(answer), sys.stdout)
        sys.stdout.write("\n")
        return 0

    event = json.load(sys.stdin)
    json.dump(asdict(decide(event)), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
