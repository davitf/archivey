#!/usr/bin/env python3
"""Decide whether a review round runs, and write the comment that closes it.

A round starts when someone adds the ``review`` label to a pull request, and the round
takes the label off again as it starts, so the label can be added for the next one.
`.github/workflows/review-loop.yml` is the wiring; this is the part worth testing. Stdlib only, no GitHub access: the workflow
collects the facts and this makes the decisions.

Two modes, each reading one JSON object on stdin and writing one on stdout.

**Decide** (the default)::

    {"labels": ["review", ...], "rounds_done": 2, "sender_type": "Bot",
     "sender_login": "claude[bot]", "repository": "davitf/archivey"}
    -> {"run": true, "round": 3, "final": false, "forced": false,
        "cap_reached": false, "reason": "...", "comment": "", "max_rounds": 5}

``rounds_done`` is how many round comments (`ROUND_MARKER`) the workflow already
posted on the pull request. Counting those, rather than keeping a counter somewhere,
means there is no state to get out of step: a round that never posted its comment
did not count, and nothing re-runs by itself either way.

**Finish** (``--finish``)::

    {"round": 3, "final": false, "head_sha": "abc...", "repository": "...",
     "verdict": {"verdict": "findings", "summary": "...", "question": ""}}
    -> {"verdict": "findings", "stop": false, "counted": true,
        "comment": "...", "reason": "..."}

``verdict`` is the file the reviewing agent wrote, or ``null`` when it wrote none.
The comment says what happened and what the implementer does next, and it carries
the marker that makes the round count.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass

#: The label that asks for a round. Adding it is the whole trigger.
REVIEW_LABEL = "review"

#: How many rounds an agent can ask for before the pull request is a person's.
#:
#: Five (davitf, 2026-09-21): the cap is the backstop that stops two agents going back
#: and forth on one pull request, not the mechanism that ends a review. A round whose
#: verdict does not ask to see the fix already says "no further round", and the
#: implementer does not add the label back (`VERDICT_STOPS`).
#:
#: Past the cap only a person's label runs a round. Who added the label is GitHub's
#: ``sender``, and an agent working in this repository adds labels as a bot account,
#: so a label is the one trigger that tells the two apart without guessing.
MAX_ROUNDS = 5

#: The first line of every round comment, and what `rounds_done` counts.
#:
#: The workflow counts only comments by ``github-actions[bot]`` that open with this,
#: so a person quoting it cannot change the count.
ROUND_MARKER = "<!-- archivey-review-round"

#: What a round's verdict says about whether the implementer asks for another one.
#:
#: The reviewer decides this, per `code-review-skill`'s addendum §0: a plain approval,
#: a conditional approval whose open findings have an obvious fix, and a comment all
#: mean "I do not need to see the result". Only "request changes" asks for another
#: look. In the two weeks to 2026-09-19 every 🔴 in this repository was raised in
#: round 1 or 2, and late rounds found almost only wording, so a round the reviewer
#: did not ask for is the back-and-forth the cap exists to bound.
#:
#: A verdict this does not recognise counts as `findings`: a reviewer whose verdict
#: cannot be read has not said it is finished, and the cap still bounds what that costs.
VERDICT_STOPS = {
    # Nothing was found at all.
    "clean": True,
    # Findings were posted, and the reviewer does not need to see them fixed. They are
    # still fixed; what ends is the re-reading.
    "approved": True,
    # 🔄 Request Changes: the next round is the implementer's to ask for.
    "findings": False,
    # A maintainer decision. Nobody asks for the next round until it is answered.
    "decision": False,
}

#: What an unreadable or unrecognised verdict is treated as. See `VERDICT_STOPS`.
DEFAULT_VERDICT = "findings"


def _footer(repository: str) -> str:
    # A relative link does not resolve from an issue comment, so spell the URL out.
    doc = f"https://github.com/{repository}/blob/main/dev-docs/review-loop.md"
    return (
        f"\n\n---\n_Posted by the [review workflow]({doc}). "
        "[Claude Code](https://claude.ai/code) wrote the review; "
        "this comment is bookkeeping._\n"
    )


# --- deciding whether a round runs ----------------------------------------------------


@dataclass(frozen=True)
class Decision:
    run: bool
    round: int
    reason: str
    #: The round about to run is the last one an agent can ask for.
    final: bool = False
    #: A person asked for a round past the cap.
    forced: bool = False
    #: An agent asked for a round past the cap. The workflow posts `comment`.
    cap_reached: bool = False
    #: What to post when the answer is worth announcing, else empty.
    comment: str = ""
    #: The cap, so the workflow's prompt has one source for it.
    max_rounds: int = MAX_ROUNDS


def decide(event: dict) -> Decision:
    """Should adding the label run a round now, and which round is it?"""
    labels = list(event.get("labels") or [])
    try:
        done = max(int(event.get("rounds_done") or 0), 0)
    except (TypeError, ValueError):
        done = 0
    nxt = done + 1
    final = nxt >= MAX_ROUNDS

    if REVIEW_LABEL not in labels:
        # The label was on the pull request when the event fired and is gone now: a
        # round that started meanwhile took it, or a person changed their mind. Either
        # way nobody is asking any more.
        return Decision(False, done, f"the {REVIEW_LABEL!r} label is no longer set")

    if nxt <= MAX_ROUNDS:
        return Decision(True, nxt, "requested by label", final=final)

    if event.get("sender_type") == "User":
        return Decision(
            True,
            nxt,
            "a person asked for a round past the cap",
            final=True,
            forced=True,
        )

    repository = str(event.get("repository") or "")
    comment = (
        f"**Not reviewed: all {MAX_ROUNDS} rounds an agent can ask for are spent.** "
        "This pull request needs a person now. A person adding the "
        f"`{REVIEW_LABEL}` label still buys another round." + _footer(repository)
    )
    return Decision(
        False,
        done,
        f"round cap spent, and {event.get('sender_login') or 'the sender'} is not a person",
        cap_reached=True,
        comment=comment,
    )


# --- finishing a round -----------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """What the round's own verdict file says, once it has been made safe to act on."""

    verdict: str
    #: The reviewer does not need another round.
    stop: bool
    summary: str
    #: Only a `decision` carries one; anything else is dropped rather than shown.
    question: str
    reason: str


def read_verdict(payload: object) -> Verdict:
    """The reviewer's verdict file, normalised.

    The reviewing agent writes this file, so it is the one input that is prose rather
    than GitHub state. Every answer names what it read, so a spelling nobody expected
    shows up in the run summary instead of quietly becoming another round.
    """
    if not isinstance(payload, dict):
        return Verdict(
            DEFAULT_VERDICT, False, "", "", "the verdict file is not a JSON object"
        )

    raw = payload.get("verdict")
    name = raw.strip().lower() if isinstance(raw, str) else ""
    summary = payload.get("summary")
    summary = summary.strip() if isinstance(summary, str) else ""
    question = payload.get("question")

    if name not in VERDICT_STOPS:
        return Verdict(
            DEFAULT_VERDICT,
            False,
            summary,
            "",
            f"verdict {raw!r} is not one this loop knows; treating it as "
            f"{DEFAULT_VERDICT!r}",
        )

    return Verdict(
        name,
        VERDICT_STOPS[name],
        summary,
        # A question on any other verdict would read as though something waits on the
        # maintainer, on a pull request where nothing does.
        question.strip() if name == "decision" and isinstance(question, str) else "",
        f"verdict {name!r}",
    )


@dataclass(frozen=True)
class Finish:
    verdict: str
    stop: bool
    #: The comment carries `ROUND_MARKER`, so this round counts towards the cap.
    counted: bool
    comment: str
    reason: str


def finish(event: dict) -> Finish:
    """The comment that closes a round: what the review said and what happens next."""
    rnd = int(event.get("round") or 0)
    final = bool(event.get("final"))
    sha = str(event.get("head_sha") or "")
    footer = _footer(str(event.get("repository") or ""))
    label = f"`{REVIEW_LABEL}`"

    if event.get("verdict") is None:
        # No marker, so nothing counts: the review did not reach a verdict, and a round
        # that died early (one on #352 did, 481 ms in) has not spent the reviewer's time.
        return Finish(
            DEFAULT_VERDICT,
            False,
            False,
            f"**Round {rnd} did not finish.** The review stopped before it reached a "
            f"verdict, so the round was not counted. Add the {label} label again to "
            "retry." + footer,
            "no verdict file",
        )

    v = read_verdict(event.get("verdict"))
    summary = f" {v.summary}" if v.summary else ""

    if v.verdict == "decision":
        body = (
            f"**Round {rnd} stopped for a decision.**{summary}\n\n"
            f"**The question:** {v.question or 'see the review above.'}\n\n"
            "The options, and what each one costs, are in the review above. Once it is "
            f"answered, add the {label} label again to carry on."
        )
    elif v.verdict == "clean":
        body = (
            f"**Round {rnd} found nothing to fix.**{summary}\n\n"
            "This is ready for a person to look at and merge."
        )
    elif v.stop:
        # Not "the review approved it": this covers ✅ Approve, the conditional
        # approval and 💬 Comment, and §0 is explicit that a comment is not an approval.
        body = (
            f"**Round {rnd}: the review does not need to see the result.**{summary}\n\n"
            "Work through any findings it posted with `address-review-findings`. No "
            f"further round is needed; adding the {label} label buys one anyway."
        )
    elif final:
        body = (
            f"**Round {rnd} posted findings, and that was the last round an agent can "
            f"ask for.**{summary}\n\n"
            "Work through them with `address-review-findings`. Nothing reviews the "
            f"result unless a person adds the {label} label."
        )
    else:
        body = (
            f"**Round {rnd} posted findings and wants to see the fixes.**{summary}\n\n"
            "Work through them with `address-review-findings`, push, and then add the "
            f"{label} label again for round {rnd + 1}. Up to {MAX_ROUNDS} rounds run "
            "this way."
        )

    marker = f"{ROUND_MARKER} n={rnd}{f' sha={sha}' if sha else ''} -->"
    return Finish(v.verdict, v.stop, True, f"{marker}\n\n{body}{footer}", v.reason)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        payload = {"_error": str(exc)}

    if "--finish" in sys.argv[1:]:
        # Never an error exit: the findings are already on the pull request, and the
        # round still has to say so and take its label off.
        if "_error" in payload:
            answer = asdict(
                Finish(DEFAULT_VERDICT, False, False, "", payload["_error"])
            )
        else:
            answer = asdict(finish(payload))
    else:
        answer = asdict(decide(payload if isinstance(payload, dict) else {}))

    json.dump(answer, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
