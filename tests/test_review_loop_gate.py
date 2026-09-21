"""The review loop's gate, exercised without GitHub.

`scripts/review_loop_gate.py` decides whether an automated review round runs, on which
pull request, and which round number it is. Everything it needs arrives as JSON, so the
interesting cases — the round cap, the quiet period, the parked labels, a fork, a
stranger asking for a review — are testable here instead of by pushing to a pull request
and watching what happens.

The cases that matter most are the ones where the answer must be *no*: a gate that
over-fires spends review credits on pull requests nobody enrolled, and one that ignores
a parked label reopens a question the maintainer was still answering.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_loop_gate.py"

_spec = importlib.util.spec_from_file_location("review_loop_gate", SCRIPT)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
# `scripts/` is not a package, so the module is loaded by path. It has to be in
# `sys.modules` before it executes: `@dataclass` looks its own module up by name.
sys.modules[_spec.name] = gate
_spec.loader.exec_module(gate)

NOW = "2026-09-19T12:00:00Z"
LONG_AGO = "2026-09-19T11:00:00Z"  # an hour before NOW: quiet
JUST_NOW = "2026-09-19T11:59:00Z"  # a minute before NOW: still being pushed to


def event(**overrides) -> dict:
    """A pull request being marked ready for review — the common single event."""
    base = {
        "event_name": "pull_request",
        "action": "ready_for_review",
        "number": 365,
        "head_sha": "a" * 40,
        "labels": ["loop:round-1"],
        "head_ref": "cursor/some-fix-1234",
        "cross_repository": False,
        "comment_body": "",
        "comment_author_association": "",
        "comment_author_login": "",
        "is_pull_request": False,
        "force": False,
    }
    return base | overrides


def candidate(**overrides) -> dict:
    """An enrolled pull request that has gone quiet — the common scan candidate."""
    base = {
        "number": 365,
        "labels": ["loop:round-1"],
        "head_ref": "cursor/some-fix-1234",
        "cross_repository": False,
        "head_sha": "a" * 40,
        "head_committed_at": LONG_AGO,
        "last_reviewed_sha": "b" * 40,
    }
    return base | overrides


def scan(*candidates: dict, now: str = NOW) -> gate.Decision:
    return gate.decide(
        {"event_name": "schedule", "now": now, "candidates": list(candidates)}
    )


# --- counting rounds ---------------------------------------------------------------


def test_round_label_is_the_only_state() -> None:
    assert gate.current_round([]) == 0
    assert gate.current_round(["loop:on", "bug"]) == 0
    assert gate.current_round(["loop:round-2"]) == 2
    # Labels are not ordered, and a stale one may linger: take the highest, not the last.
    assert gate.current_round(["loop:round-3", "loop:round-1"]) == 3
    assert gate.current_round(["loop:round-x", "loop:roundup"]) == 0


def test_each_round_advances_one() -> None:
    assert scan(candidate(labels=["loop:round-1"])).round == 2
    assert scan(candidate(labels=["loop:round-2"])).round == 3


def test_the_cap_stops_the_scan() -> None:
    spent = [f"loop:round-{gate.MAX_ROUNDS}"]
    decision = scan(candidate(labels=spent))
    assert not decision.run
    # Nothing was eligible, so the scan reports on the tick rather than on one PR.
    assert decision.reason == "no pull request is waiting for a round"

    # The PR-level answer is the one that carries the cap.
    parked = gate._scheduled(
        candidate(labels=spent), gate.parse_time(NOW), gate.timedelta(0)
    )
    assert not parked.run
    assert parked.cap_reached
    # The round stays at what was actually done, so the hand-back can name the number.
    assert parked.round == gate.MAX_ROUNDS

    # And the round before it still runs, so the cap is off by nothing.
    assert scan(candidate(labels=[f"loop:round-{gate.MAX_ROUNDS - 1}"])).run


def test_the_cap_is_five_rounds() -> None:
    """The number itself, pinned where moving it has to be deliberate.

    It was three until 2026-09-21, when it turned out to be the only thing ever
    stopping the loop: every pull request the loop finished carried `loop:round-3`.
    Five is safe because the verdict now stops the loop first — see `VERDICT_STOPS`
    and the tests below it — so this is the backstop rather than the mechanism.
    """
    assert gate.MAX_ROUNDS == 5
    # A ceiling that grows with the cap would have gone to ten. Three spare rounds is
    # what puts it out of reach of a person buying one more, and that is a constant.
    assert gate.MAX_FORCED_ROUNDS == 8


def test_the_round_before_the_cap_announces_itself_as_the_last() -> None:
    # The last round has to say so as it posts: nothing after it notices.
    assert scan(candidate(labels=[f"loop:round-{gate.MAX_ROUNDS - 1}"])).final
    assert not scan(candidate(labels=[f"loop:round-{gate.MAX_ROUNDS - 2}"])).final


# --- the quiet period ----------------------------------------------------------------


def test_a_branch_still_being_pushed_to_is_left_alone() -> None:
    """The reason the loop is scheduled rather than push-driven.

    Four commits landed on the first pull request the loop saw inside thirteen minutes.
    A round per push would have spent the whole cap on half-written work.
    """
    decision = scan(candidate(head_committed_at=JUST_NOW))
    assert not decision.run

    per_pr = gate._scheduled(
        candidate(head_committed_at=JUST_NOW),
        gate.parse_time(NOW),
        gate.timedelta(minutes=gate.QUIET_MINUTES),
    )
    assert not per_pr.run
    assert "quiet" in per_pr.reason


def test_the_quiet_period_is_measured_from_the_last_commit() -> None:
    """Pin the boundary to `QUIET_MINUTES` itself, so raising it cannot drift silently.

    The threshold moved from ten minutes to thirty on 2026-09-19 because ten was short
    enough that an ordinary pause mid-task — a long test run, a slow tool call — read
    as "the implementer has finished" and spent a round on half-written code. The two
    cases below are a minute either side of whatever the constant now says.
    """
    now = gate.parse_time(NOW)
    quiet = gate.timedelta(minutes=gate.QUIET_MINUTES)

    def at(minutes_ago: int) -> str:
        return (now - gate.timedelta(minutes=minutes_ago)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    just_short = gate._scheduled(
        candidate(head_committed_at=at(gate.QUIET_MINUTES - 1)), now, quiet
    )
    assert not just_short.run
    assert "quiet" in just_short.reason

    assert gate._scheduled(
        candidate(head_committed_at=at(gate.QUIET_MINUTES + 1)), now, quiet
    ).run


def test_the_same_commit_is_not_reviewed_twice() -> None:
    """The scan runs every few minutes; without this it would re-review on every tick."""
    decision = scan(candidate(last_reviewed_sha="a" * 40))
    assert not decision.run

    # A push moves the head, and the round is due again.
    assert scan(candidate(head_sha="c" * 40, last_reviewed_sha="a" * 40)).run


def test_a_commit_with_no_timestamp_is_skipped_rather_than_reviewed() -> None:
    assert not scan(candidate(head_committed_at="")).run
    assert not scan(candidate(head_committed_at="not a date")).run


def test_an_unknown_scan_time_fails_closed() -> None:
    """Not knowing the time is not the same as knowing the branch is quiet.

    The workflow always passes `date -u`, so this is about which way the gate falls
    over when it does not: the wrong direction spends a review on a branch that is
    still being written.
    """
    assert not scan(candidate(), now="").run
    assert not scan(candidate(), now="not a date").run


def test_one_pull_request_per_tick_longest_waiting_first() -> None:
    decision = scan(
        candidate(number=400, head_committed_at="2026-09-19T11:50:00Z"),
        candidate(number=365, head_committed_at="2026-09-19T09:00:00Z"),
        candidate(number=380, head_committed_at="2026-09-19T10:00:00Z"),
    )
    assert decision.run
    assert decision.pr == 365


def test_ties_break_on_the_pull_request_number() -> None:
    # Two agents finishing in the same second must not make the choice arbitrary: a
    # retried tick has to pick the same pull request as the one it is retrying.
    decision = scan(candidate(number=400), candidate(number=365))
    assert decision.pr == 365


def test_an_empty_scan_says_so_without_naming_a_pull_request() -> None:
    decision = scan()
    assert not decision.run
    assert decision.pr == 0


# --- stopping ----------------------------------------------------------------------


@pytest.mark.parametrize("label", ["loop:decision", "loop:hold", "loop:done"])
def test_parked_labels_stop_an_automatic_round(label: str) -> None:
    assert not scan(candidate(labels=["loop:round-1", label])).run

    per_pr = gate._scheduled(
        candidate(labels=["loop:round-1", label]),
        gate.parse_time(NOW),
        gate.timedelta(0),
    )
    assert label in per_pr.reason
    assert (
        not per_pr.cap_reached
    )  # not the cap — a different "no", and a different message


def test_loop_off_beats_even_an_explicit_request() -> None:
    decision = gate.decide(
        event(
            event_name="issue_comment",
            labels=["loop:round-1", "loop:off"],
            is_pull_request=True,
            comment_body="@claude review",
            comment_author_association="OWNER",
        )
    )
    assert not decision.run
    assert not scan(candidate(labels=["loop:round-1", "loop:off"])).run


def test_a_fork_never_runs() -> None:
    # A fork's `pull_request` run has no secrets, so this would fail rather than review.
    assert not gate.decide(event(cross_repository=True)).run
    assert not scan(candidate(cross_repository=True)).run


# --- enrolment ---------------------------------------------------------------------


def test_opening_a_cursor_branch_enrols_it_without_reviewing_it() -> None:
    decision = gate.decide(
        event(action="opened", labels=[], head_ref="cursor/fix-1234")
    )
    # Nothing to review yet: the agent that opened it is still pushing.
    assert not decision.run
    assert decision.enrol


def test_any_other_new_branch_needs_the_opt_in_label() -> None:
    assert not gate.decide(
        event(action="opened", labels=[], head_ref="claude/some-work")
    ).enrol
    assert gate.decide(
        event(action="opened", labels=["loop:on"], head_ref="claude/some-work")
    ).enrol


def test_ready_for_review_does_not_wait_for_the_quiet_period() -> None:
    """An explicit "this is finished" is the signal the quiet period exists to infer."""
    decision = gate.decide(event(action="ready_for_review", labels=[]))
    assert decision.run
    assert decision.round == 1
    assert decision.enrol  # and it joins the loop, so later rounds are scanned for


def test_a_push_is_not_an_event_the_loop_acts_on() -> None:
    decision = gate.decide(event(action="synchronize"))
    assert not decision.run
    assert not decision.enrol


def test_pull_requests_that_predate_the_loop_stay_out_of_it() -> None:
    """The guard that keeps this from firing on every `cursor/*` pull request.

    The branch prefix enrols a pull request once, when it opens. The scan reads only
    the label, so a pull request that was already open when the loop landed is never
    picked up until someone adds `loop:on` deliberately.
    """
    decision = gate._scheduled(
        candidate(labels=[], head_ref="cursor/old"),
        gate.parse_time(NOW),
        gate.timedelta(0),
    )
    assert not decision.run
    assert "not enrolled" in decision.reason

    assert scan(candidate(labels=["loop:on"], head_ref="cursor/old")).run


# --- asking for a round by hand -----------------------------------------------------


def test_a_collaborator_can_restart_a_parked_loop() -> None:
    decision = gate.decide(
        event(
            event_name="issue_comment",
            labels=["loop:round-1", "loop:decision"],
            is_pull_request=True,
            comment_body="@claude review please\n\nAnswered below — option B.",
            comment_author_association="OWNER",
        )
    )
    assert decision.run
    assert decision.forced
    assert decision.round == 2


def test_a_comment_can_buy_a_round_past_the_cap() -> None:
    decision = gate.decide(
        event(
            event_name="issue_comment",
            labels=[f"loop:round-{gate.MAX_ROUNDS}", "loop:done"],
            is_pull_request=True,
            comment_body="@claude review",
            comment_author_association="OWNER",
        )
    )
    assert decision.run
    assert decision.round == gate.MAX_ROUNDS + 1
    # And it is still past the automatic cap. This assertion used to read `not final`,
    # on the reasoning that whoever asked could ask again — true, and beside the point:
    # `final` is what puts `loop:done` back after the verdict step clears the stale
    # parks, so leaving it false took the label off a pull request the scan and the
    # bots both refuse. `test_a_bought_round_past_the_cap_is_still_the_last_automatic_one`
    # covers the consequence.
    assert decision.final


@pytest.mark.parametrize("login", ["cursor[bot]", "claude[bot]"])
def test_the_implementing_agent_can_say_it_has_finished(login: str) -> None:
    """The explicit signal, preferred over waiting out the quiet period.

    Neither bot is a repository collaborator — GitHub reports `NONE` — so they get
    here on their login rather than on their association. Both hosts matter:
    `address-review-findings` §7 tells whichever of them holds the branch to send
    this, so a gate that knew only one would make the instruction a lie on the other.
    """
    decision = gate.decide(
        event(
            event_name="issue_comment",
            labels=["loop:round-1"],
            is_pull_request=True,
            comment_body="@claude review\n\nFindings addressed and pushed.",
            comment_author_association="NONE",
            comment_author_login=login,
        )
    )
    assert decision.run
    assert decision.round == 2
    # Not forced: an agent saying it is done cannot reach past a park or the cap.
    assert not decision.forced


@pytest.mark.parametrize(
    ("labels", "why"),
    [
        ([f"loop:round-{gate.MAX_ROUNDS}"], "the cap"),
        (["loop:round-1", "loop:decision"], "a maintainer decision"),
        (["loop:round-1", "loop:hold"], "a hold"),
        ([], "never having been enrolled, on a branch that predates the loop"),
    ],
)
def test_the_agent_cannot_talk_its_way_past_a_stop(labels: list[str], why: str) -> None:
    """The whole difference between the bot path and the human one.

    A person asking for a round is asking past the cap and past the park they set
    themselves. An agent reporting that it has stopped pushing is not asking for
    anything, so every stop still holds — otherwise an agent that fixes, comments,
    fixes and comments could run the loop indefinitely.
    """
    decision = gate.decide(
        event(
            event_name="issue_comment",
            labels=labels,
            is_pull_request=True,
            comment_body="@claude review",
            comment_author_association="NONE",
            comment_author_login="cursor[bot]",
        )
    )
    assert not decision.run, why


def test_the_reviewers_hand_back_enrols_a_branch_that_never_did() -> None:
    """Cursor approving a Claude branch has to be able to start the pass from zero.

    Only `cursor/*` auto-enrols, so a `claude/*` pull request reaches this comment
    with no loop label at all and the hand-back used to be refused with nothing
    posted to say so. The request itself is the enrolment signal here; the branch
    prefix is what enrols the other direction.
    """
    decision = gate.decide(
        event(
            event_name="issue_comment",
            labels=[],
            head_ref="claude/project-thread-blk7eo",
            is_pull_request=True,
            comment_body="@claude review",
            comment_author_association="NONE",
            comment_author_login="cursor[bot]",
        )
    )
    assert decision.run
    assert decision.enrol
    assert decision.round == 1
    # Enrolling is not the same as forcing: the cap and the parks still apply.
    assert not decision.forced


def test_a_park_stops_the_hand_back_before_it_can_enrol() -> None:
    """Order matters in the bot branch, and nothing else pins it.

    Enrolment now has an escape hatch, so the parks have to be read first — a
    pull request parked on a maintainer decision must not be restarted by an
    agent's comment just because it carries no `loop:on`.
    """
    decision = gate.decide(
        event(
            event_name="issue_comment",
            labels=["loop:decision"],
            head_ref="claude/project-thread-blk7eo",
            is_pull_request=True,
            comment_body="@claude review",
            comment_author_association="NONE",
            comment_author_login="cursor[bot]",
        )
    )
    assert not decision.run
    assert not decision.enrol


def test_a_fork_cannot_buy_a_round_by_commenting() -> None:
    """The comment path is the only one where a fork round would really start.

    `pull_request` and the scan both refuse a fork earlier, and the recorded reason
    for the `pull_request` guard — a fork run has no secrets, so the review fails
    anyway — is not true here: an `issue_comment` run is on the base repository.
    """
    decision = gate.decide(
        event(
            event_name="issue_comment",
            labels=["loop:on"],
            cross_repository=True,
            is_pull_request=True,
            comment_body="@claude review",
            comment_author_association="OWNER",
        )
    )
    assert not decision.run
    assert "fork" in decision.reason


def test_marking_a_parked_pull_request_ready_leaves_its_status_alone() -> None:
    """`enrol` is what runs the step that rewrites the status comment.

    On a parked pull request that comment is the only thing saying why nothing is
    happening — the maintainer's question, or why a round died. Setting `enrol` on a
    refusal replaced it with "a review starts by itself once the branch goes quiet",
    a promise nothing would honour. A parked pull request is already enrolled anyway.
    """
    for park in ("loop:decision", "loop:hold", "loop:done"):
        decision = gate.decide(event(labels=["loop:round-1", park]))
        assert not decision.run
        assert not decision.enrol, park


def test_a_refusal_names_the_threshold_that_produced_it() -> None:
    """The reason strings interpolated the constant while the rule used the argument.

    Nothing asserted on them, so the two could drift into a message that described a
    rule it was not produced by.
    """
    decision = gate._scheduled(
        candidate(head_committed_at=JUST_NOW),
        gate.parse_time(NOW),
        gate.timedelta(minutes=5),
    )
    assert not decision.run
    assert "under 5m" in decision.reason

    ran = gate._scheduled(
        candidate(head_committed_at=JUST_NOW),
        gate.parse_time(NOW),
        gate.timedelta(0),
    )
    assert ran.run
    assert "for 0 minutes" in ran.reason


def test_an_unknown_bot_is_still_a_stranger() -> None:
    decision = gate.decide(
        event(
            event_name="issue_comment",
            labels=["loop:round-1"],
            is_pull_request=True,
            comment_body="@claude review",
            comment_author_association="NONE",
            comment_author_login="dependabot[bot]",
        )
    )
    assert not decision.run


def test_no_comment_runs_forever_however_entitled_the_commenter() -> None:
    """The ceiling the forced path needs because a forced round ignores every stop.

    An agent posting through the maintainer's account is `OWNER` like the maintainer,
    and `address-review-findings` §7 tells it to send this phrase after every round.
    Without a ceiling that is fix-comment-fix-comment at full review cost, forever.
    """
    owner = {
        "event_name": "issue_comment",
        "is_pull_request": True,
        "comment_body": "@claude review",
        "comment_author_association": "OWNER",
        "comment_author_login": "davitf",
    }
    last = gate.MAX_FORCED_ROUNDS
    assert gate.decide(event(labels=[f"loop:round-{last - 1}"], **owner)).run
    spent = gate.decide(event(labels=[f"loop:round-{last}"], **owner))
    assert not spent.run
    assert spent.cap_reached

    # A bot is bounded long before this, by the ordinary cap.
    assert not gate.decide(
        event(
            labels=[f"loop:round-{gate.MAX_ROUNDS}"],
            **(
                owner
                | {
                    "comment_author_login": "cursor[bot]",
                    "comment_author_association": "NONE",
                }
            ),
        )
    ).run

    # The deliberate override survives, because a button in the GitHub UI is not
    # something an agent presses.
    assert gate.decide(
        event(
            event_name="workflow_dispatch",
            labels=[f"loop:round-{gate.MAX_FORCED_ROUNDS + 4}"],
            force=True,
        )
    ).run


def test_a_stranger_cannot_spend_review_credits() -> None:
    decision = gate.decide(
        event(
            event_name="issue_comment",
            is_pull_request=True,
            comment_body="@claude review",
            comment_author_association="NONE",
        )
    )
    assert not decision.run


@pytest.mark.parametrize(
    "body",
    [
        "thanks @claude",
        "the review loop is great",
        "",
    ],
)
def test_only_the_trigger_phrase_starts_a_round(body: str) -> None:
    decision = gate.decide(
        event(
            event_name="issue_comment",
            is_pull_request=True,
            comment_body=body,
            comment_author_association="OWNER",
        )
    )
    assert not decision.run


def test_a_comment_on_an_issue_is_not_a_pull_request() -> None:
    decision = gate.decide(
        event(
            event_name="issue_comment",
            is_pull_request=False,
            comment_body="@claude review",
            comment_author_association="OWNER",
        )
    )
    assert not decision.run


def test_the_trigger_phrase_never_fires_both_workflows() -> None:
    """The two `issue_comment` workflows must partition comments, not overlap.

    `.github/workflows/claude.yml` runs the general-purpose assistant on any comment
    containing `@claude`, and skips the ones containing `@claude review` so this loop
    can have them. That skip is a GitHub `contains()` — a case-insensitive substring
    test, with no regex available — so the gate must never accept a comment that
    `contains()` would let through to the assistant. The gate being the stricter of
    the two is fine and deliberate: a comment that merely writes about the phrase
    then runs neither workflow, which is the outcome this loop wants.
    """

    def github_contains(body: str) -> bool:
        """What `contains(github.event.comment.body, '@claude review')` does."""
        return "@claude review" in body.lower()

    for body in [
        "@claude review",
        "@Claude Review please",
        "\n@claude review",
        "answered — option B. @claude review",
        "@claude  review",  # two spaces: contains() says no, so the gate must too
        "@claude reviewer",
        "@claude what do you think?",
        "thanks @claude",
        "nothing to see here",
        "",
    ]:
        if gate.COMMENT_TRIGGER.match(body):
            assert github_contains(body), body


def test_writing_about_the_trigger_is_not_asking_for_a_round() -> None:
    """The loop's own paperwork quotes its trigger phrase, and must not trip on it.

    All four bodies below are real shapes: the workflow's hand-back comments, a
    reviewer's maintainer question, a dispositions comment, and this module's own
    docstrings. The first three are posted on the pull request the loop is running
    on, by accounts the gate trusts. Before the phrase was anchored, a dispositions
    comment on #369 spent a forced round on the pull request that was fixing the loop.
    """

    for body in [
        "Round 3 of 3 is spent. Comment `@claude review` to buy another round.",
        "Options\n - A — treat a comment carrying `@claude review` as ordinary.",
        "C3 — fixed in 3f24fa8. The gate now accepts `@claude review` from claude[bot].",
        "I answered above; no need to re-run. (Not writing the trigger phrase here.)",
    ]:
        assert not gate.decide(
            event(
                event_name="issue_comment",
                is_pull_request=True,
                comment_body=body,
                comment_author_association="OWNER",
            )
        ).run, body


def test_asking_for_a_round_still_works_at_the_top_of_a_comment() -> None:
    """The anchor must not cost the escape hatch it is guarding."""

    for body in [
        "@claude review",
        "  @claude review\n\nanswered option B above.",
        "@Claude Review — the decision is settled, carry on.",
    ]:
        decision = gate.decide(
            event(
                event_name="issue_comment",
                is_pull_request=True,
                comment_body=body,
                comment_author_association="OWNER",
            )
        )
        assert decision.run, body
        assert decision.forced, body


def test_manual_dispatch_respects_the_cap_unless_forced() -> None:
    spent = event(
        event_name="workflow_dispatch", labels=[f"loop:round-{gate.MAX_ROUNDS}"]
    )
    assert not gate.decide(spent).run
    assert gate.decide(spent | {"force": True}).run


# --- the wiring the workflow depends on ---------------------------------------------


def test_every_answer_names_the_pull_request_it_is_about() -> None:
    """The workflow reads the pull request number back off the gate, not off the event.

    A scheduled tick has no pull request in its event payload at all, so the gate is
    the only thing that knows which one the rest of the job is acting on.
    """
    assert gate.decide(event(number=365)).pr == 365
    assert gate.decide(event(number=365)).head_sha == "a" * 40
    assert scan(candidate(number=380, head_sha="d" * 40)).pr == 380
    assert scan(candidate(number=380, head_sha="d" * 40)).head_sha == "d" * 40


def test_the_script_reads_stdin_and_writes_json() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(event(labels=["loop:round-1"])),
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    # The workflow reads exactly these keys out with `jq`.
    assert payload.keys() == {
        "run",
        "round",
        "reason",
        "pr",
        "head_sha",
        "head_ref",
        "cap_reached",
        "forced",
        "enrol",
        "final",
        "max_rounds",
        "max_forced_rounds",
    }
    assert payload["run"] is True
    assert payload["round"] == 2
    assert payload["pr"] == 365


def test_a_bought_round_past_the_cap_is_still_the_last_automatic_one() -> None:
    """`final` means the automatic loop is spent, not that nobody can buy another.

    A forced round used to leave `final` false on the reasoning that whoever bought it
    could buy another. But the workflow clears every stale park as a round runs and
    re-adds `loop:done` only when `final` is set, so a bought round 4 with findings took
    `loop:done` off and never put it back: the scan refuses it (`nxt > MAX_ROUNDS`), a
    bot's comment refuses it, and the status comment promised a round that could not
    come. `loop:done` marks the end of the *automatic* loop, which round 4 is past by
    definition however it was bought.
    """
    for event_name, extra in [
        ("issue_comment", {"comment_author_association": "OWNER"}),
        ("workflow_dispatch", {"force": True}),
    ]:
        decision = gate.decide(
            event(
                event_name=event_name,
                is_pull_request=True,
                comment_body="@claude review",
                labels=[f"loop:round-{gate.MAX_ROUNDS}", "loop:done"],
                **extra,
            )
        )
        assert decision.run, event_name
        assert decision.round == gate.MAX_ROUNDS + 1, event_name
        assert decision.forced, event_name
        assert decision.final, event_name

    # Below the cap a bought round is genuinely not the last one.
    early = gate.decide(
        event(
            event_name="issue_comment",
            is_pull_request=True,
            comment_body="@claude review",
            labels=["loop:round-1"],
            comment_author_association="OWNER",
        )
    )
    assert early.run and early.forced and not early.final


def test_a_stranger_cannot_move_the_labels_by_naming_the_ceiling() -> None:
    """The forced ceiling binds the forced path only, and nothing before it.

    `cap_reached` is not an inert field: the workflow's hand-back step keys on it, adds
    `loop:done` and rewrites the status comment. Checking the ceiling ahead of the trust
    tests handed that write to anyone who could comment. A stranger has to fall through
    to the same refusal they get at every other round.
    """
    decision = gate.decide(
        event(
            event_name="issue_comment",
            is_pull_request=True,
            comment_body="@claude review",
            labels=[f"loop:round-{gate.MAX_FORCED_ROUNDS}"],
            comment_author_association="NONE",
            comment_author_login="dependabot[bot]",
        )
    )
    assert not decision.run
    assert not decision.cap_reached
    assert "collaborator" in decision.reason

    # The ceiling still binds the path it was written for.
    spent = gate.decide(
        event(
            event_name="issue_comment",
            is_pull_request=True,
            comment_body="@claude review",
            labels=[f"loop:round-{gate.MAX_FORCED_ROUNDS}"],
            comment_author_association="OWNER",
        )
    )
    assert not spent.run
    assert spent.cap_reached
    assert "ceiling" in spent.reason


def test_the_trigger_is_anchored_in_the_pattern_not_only_in_the_call() -> None:
    """The anchor must survive someone swapping `.match` for `.search`.

    Relying on the call site made the paperwork-quoting bug one substitution away from
    returning, and nothing at the call site says so.
    """
    quoting = "C3 fixed. Use @claude review from bots."
    assert not gate.COMMENT_TRIGGER.search(quoting)
    assert not gate.COMMENT_TRIGGER.match(quoting)
    assert gate.COMMENT_TRIGGER.search("@claude review")


def test_the_head_branch_comes_back_so_the_ping_can_be_addressed() -> None:
    """The workflow asks the branch who is fixing, and reads it off the gate.

    A Cursor mention used to be hardcoded in the findings ping, which is right only while
    Cursor is the implementer. When Claude implements and Cursor reviews, that comment
    handed the fixes to the agent that had just written them up. The branch prefix is
    the same signal the gate already uses to enrol a new pull request, so it comes out
    of the gate rather than being fetched again in bash.
    """
    ready = gate.decide(
        event(
            event_name="pull_request",
            action="ready_for_review",
            head_ref="cursor/delegating-stream-flags-8161",
            labels=["loop:on"],
        )
    )
    assert ready.run
    assert ready.head_ref == "cursor/delegating-stream-flags-8161"

    # The scan carries it too, since a scheduled round posts the same ping.
    assert (
        scan(candidate(head_ref="claude/project-thread-blk7eo")).head_ref
        == "claude/project-thread-blk7eo"
    )

    # Absent from the payload is the empty string, never None: the workflow interpolates
    # it into a `case`, where a null would read as the literal "null".
    bare = event(labels=["loop:round-1"])
    del bare["head_ref"]
    assert gate.decide(bare).head_ref == ""


# --- the findings ping's two addressees ---------------------------------------------
#
# These read the workflow file rather than a copy of it, because the thing being
# pinned is one shell block in `.github/workflows/review-loop.yml` and a copy would
# drift silently — which is the exact failure they exist to catch.

WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "review-loop.yml"
)

#: `run: |` blocks in this file are indented ten spaces, so stripping that gives the
#: shell back verbatim, heredoc bodies included.
_BLOCK_INDENT = " " * 10


def _shell_between(first: str, last: str) -> str:
    """Lift one shell fragment out of the workflow, dedented enough to run."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == first)
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == last)
    return "\n".join(
        line[len(_BLOCK_INDENT) :] if line.startswith(_BLOCK_INDENT) else line
        for line in lines[start : end + 1]
    )


def _bash(script: str) -> str:
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - Windows runners without a shell
        pytest.skip("no bash to run the workflow fragment with")
    result = subprocess.run(  # noqa: S603 - fixed argv, fragment comes from the repo
        [bash, "-c", script],
        text=True,
        capture_output=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "entry": "the `/address-review` command",
            "ROUND": "1",
            "next": "2",
        },
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize(
    ("head_ref", "github_handle", "linear_handle"),
    [
        ("cursor/delegating-stream-flags-8161", "@cursoragent", "@cursor"),
        ("claude/project-thread-o03uuf", "@claude", "@claude"),
    ],
)
def test_the_ping_is_addressed_differently_on_the_two_surfaces(
    head_ref: str, github_handle: str, linear_handle: str
) -> None:
    """Cursor answers to `@cursoragent` on GitHub and `@cursor` on Linear.

    Neither spelling works on the other surface: on GitHub `cursor` is the company's
    organization account, so the ping notified an org and woke no agent for as long as
    it said that, and `@cursoragent` on Linear mentions nobody at all. One handle used
    twice is wrong whichever one is picked, which is why there are two variables.
    """
    picked = _bash(
        f"HEAD_REF={head_ref!r}\n"
        + _shell_between('case "$HEAD_REF" in', "esac")
        + '\nprintf "%s %s" "$fixer" "$linear_fixer"'
    )
    assert picked == f"{github_handle} {linear_handle}"


def test_both_surfaces_get_the_same_ping_text() -> None:
    """The bodies must differ in the handle and in nothing else.

    They were one file copied to both surfaces until the GitHub handle was corrected,
    at which point the Linear copy silently started mentioning nobody. Emitting the
    heredoc twice would let the texts drift instead; one function, called twice, is
    what stops both.
    """
    func = _shell_between("ping_body() {", "}")
    github = _bash(f'{func}\nping_body "@cursoragent"')
    linear = _bash(f'{func}\nping_body "@cursor"')

    assert github.startswith("@cursoragent Please work through")
    assert linear.startswith("@cursor Please work through")
    assert "@cursoragent" not in linear
    assert github.split("\n", 1)[1] == linear.split("\n", 1)[1]


# --- the verdict a round ends with ---------------------------------------------------


def verdict(**overrides) -> gate.Verdict:
    payload = {"verdict": "findings", "summary": "s", "question": ""} | overrides
    return gate.read_verdict(payload)


def test_a_review_that_does_not_ask_to_see_the_fix_ends_the_loop() -> None:
    """The stopping rule the cap used to stand in for.

    `code-review-skill`'s addendum §0 already gives the reviewer four verdicts, two of
    which mean "I do not need to see the result". The loop read every round that posted
    anything as "findings" and scheduled the next one, so the only thing that ever
    stopped it was the counter — which is why raising the cap needed this first.
    """
    assert verdict(verdict="clean").stop
    assert verdict(verdict="approved").stop

    # A round that asks for another look is what buys one.
    assert not verdict(verdict="findings").stop
    # A decision parks rather than stops: the round counter is untouched, so answering
    # it resumes where this round left off.
    assert not verdict(verdict="decision").stop


def test_a_verdict_the_loop_cannot_read_still_gets_an_answer() -> None:
    """Every input here is written by an agent in prose, so nothing may fall through.

    The shell `case` this replaced had no way to tell "the reviewer asked for another
    round" from "the reviewer wrote something unexpected": both landed in the findings
    arm, silently. Unrecognised is still treated as `findings` — a reviewer whose
    verdict cannot be read has not said it is finished — but the reason says so, and
    the cap still bounds what it costs.
    """
    for payload in ["approve", "APPROVED?", "", None, 3]:
        answer = gate.read_verdict({"verdict": payload})
        assert answer.verdict == gate.DEFAULT_VERDICT, payload
        assert not answer.stop, payload
        assert "not one this loop knows" in answer.reason, payload

    for payload in [[], "not an object", None]:
        answer = gate.read_verdict(payload)
        assert answer.verdict == gate.DEFAULT_VERDICT
        assert "not a JSON object" in answer.reason

    # Spelling and spacing are the reviewer's, not a contract.
    assert gate.read_verdict({"verdict": "  Approved\n"}).verdict == "approved"


def test_a_question_belongs_to_a_decision_and_to_nothing_else() -> None:
    """A question on any other verdict reads as though something waits on the maintainer.

    The status comment prints `question` under "**The question:**" and says nothing
    further happens until it is answered. On a pull request that is not parked, that is
    a sentence nobody can act on and that contradicts the round it appears in.
    """
    assert verdict(verdict="decision", question="Fix here or file it?").question
    for name in ["clean", "approved", "findings"]:
        assert not verdict(verdict=name, question="Fix here or file it?").question


def test_the_verdict_mode_reads_stdin_and_writes_json() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--verdict"],
        input=json.dumps({"verdict": "approved", "summary": "Two nits left."}),
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    # The workflow reads exactly these keys out with `jq`.
    assert payload.keys() == {"verdict", "stop", "summary", "question", "reason"}
    assert payload["stop"] is True
    assert payload["summary"] == "Two nits left."


def test_an_unreadable_verdict_file_does_not_fail_the_round() -> None:
    """The findings are already on the pull request by the time this runs.

    Exiting non-zero here would fail a round that had done its work, and the step that
    parks a pull request whose review died is upstream of this one — so the run would
    go red with the loop's state saying a round is still in flight.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--verdict"],
        input="{not json at all",
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["verdict"] == gate.DEFAULT_VERDICT
    assert not payload["stop"]
    assert "unreadable verdict" in payload["reason"]


# --- what the workflow has to keep in step with the gate ------------------------------


def test_the_workflow_asks_the_gate_what_the_verdict_means() -> None:
    """The verdict is decided in one place, and it is the tested one."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/review_loop_gate.py --verdict" in text
    # Not read straight out of the reviewer's own file any more: that is what let an
    # unrecognised spelling become another round with nothing saying so.
    assert "jq -r .verdict /tmp/loop-verdict.json" not in text


def test_the_workflow_takes_the_cap_from_the_gate() -> None:
    """Moving `MAX_ROUNDS` must move the labels and the prose with it.

    The label list used to name rounds 1 to 3 one line each, and three sentences said
    "three rounds" in their own words. Each is a copy that a later change to the number
    leaves behind: a round with no label cannot be counted, and a status comment that
    promises the wrong number is read by whoever is deciding whether to merge.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "max_rounds=$(jq -r .max_rounds /tmp/gate.json)" in text
    assert 'for n in $(seq 1 "$MAX_LABELLED_ROUND"); do' in text
    for stale in ['ensure "loop:round-1"', "Three review rounds", "Up to three rounds"]:
        assert stale not in text, stale


def test_every_round_that_can_run_has_a_label_waiting_for_it() -> None:
    """The label list is bounded by the forced ceiling, not by the cap.

    A collaborator's comment buys rounds past the cap, and the verdict step opens by
    applying `loop:round-$ROUND` under `set -e`: a round whose label does not exist
    loses every write after that line — the `loop:on` removal, the stale parks, and the
    status comment, which is the only thing on the pull request that says where the
    loop stands. The review itself has already been posted by then, so the failure is
    invisible unless someone opens the run.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "max_forced_rounds=$(jq -r .max_forced_rounds /tmp/gate.json)" in text
    assert "MAX_LABELLED_ROUND: ${{ steps.gate.outputs.max_forced_rounds }}" in text

    # The label the verdict step applies is the round the gate answered with, and the
    # furthest that can go is the ceiling the labels are created up to.
    furthest = gate.decide(
        event(
            event_name="issue_comment",
            is_pull_request=True,
            comment_body="@claude review",
            comment_author_association="OWNER",
            labels=[f"loop:round-{gate.MAX_FORCED_ROUNDS - 1}"],
        )
    )
    assert furthest.run
    assert furthest.round <= furthest.max_forced_rounds


@pytest.mark.parametrize(
    ("over", "expected", "forbidden"),
    [
        ("false", "up to 5 run", "last round"),
        ("true", "last round", "@claude review"),
    ],
)
def test_the_ping_promises_a_round_only_when_one_is_coming(
    over: str, expected: str, forbidden: str
) -> None:
    """A ping that asks for the next round after the last one asks for nothing.

    It read "This is round 3 of 3 … that starts round 4 straight away" on every final
    round: the agent does as it is told, comments, and the gate refuses the round the
    comment was for — a runner woken to say no, and an implementer told the opposite of
    what the status comment on the same pull request says.
    """
    # Anchored on the comment rather than on the `if`, because the status comment a
    # few lines up branches on `$over` too and `_shell_between` takes the first match.
    fragment = _shell_between(
        '# What the ping promises has to be what happens. It said "that starts', "}"
    )
    body = _bash(f'over={over}\nMAX_ROUNDS=5\n{fragment}\nping_body "@claude"')
    assert expected in body
    assert forbidden not in body
    # Whatever it says about rounds, it still asks for the findings to be fixed and it
    # still carries the footer that marks it as an agent's comment.
    assert body.startswith("@claude Please work through")
    assert "_Generated by [Claude Code](https://claude.ai/code)_" in body
