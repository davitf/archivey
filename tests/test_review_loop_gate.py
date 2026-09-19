"""The review loop's gate, exercised without GitHub.

`scripts/review_loop_gate.py` decides whether an automated review round runs and which
round number it is. Everything it needs arrives as JSON, so the interesting cases —
the round cap, the parked labels, a fork, a stranger asking for a review — are testable
here instead of by pushing to a pull request and watching what happens.

The cases that matter most are the ones where the answer must be *no*: a gate that
over-fires spends review credits on pull requests nobody enrolled, and one that ignores
a parked label reopens a question the maintainer was still answering.
"""

from __future__ import annotations

import importlib.util
import json
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


def event(**overrides) -> dict:
    """A push to an enrolled, non-draft, in-repo pull request — the common case."""
    base = {
        "event_name": "pull_request",
        "action": "synchronize",
        "labels": ["loop:round-1"],
        "draft": False,
        "head_ref": "cursor/some-fix-1234",
        "cross_repository": False,
        "comment_body": "",
        "comment_author_association": "",
        "is_pull_request": False,
        "force": False,
    }
    return base | overrides


# --- counting rounds ---------------------------------------------------------------


def test_round_label_is_the_only_state() -> None:
    assert gate.current_round([]) == 0
    assert gate.current_round(["loop:on", "bug"]) == 0
    assert gate.current_round(["loop:round-2"]) == 2
    # Labels are not ordered, and a stale one may linger: take the highest, not the last.
    assert gate.current_round(["loop:round-3", "loop:round-1"]) == 3
    assert gate.current_round(["loop:round-x", "loop:roundup"]) == 0


def test_each_push_advances_one_round() -> None:
    assert gate.decide(event(labels=["loop:round-1"])).round == 2
    assert gate.decide(event(labels=["loop:round-2"])).round == 3


def test_the_cap_is_three_rounds() -> None:
    decision = gate.decide(event(labels=["loop:round-3"]))
    assert not decision.run
    assert decision.cap_reached
    # The round stays at what was actually done, so the hand-back message can say "3".
    assert decision.round == 3


# --- stopping ----------------------------------------------------------------------


@pytest.mark.parametrize("label", ["loop:decision", "loop:hold", "loop:done"])
def test_parked_labels_stop_an_automatic_round(label: str) -> None:
    decision = gate.decide(event(labels=["loop:round-1", label]))
    assert not decision.run
    assert label in decision.reason
    assert (
        not decision.cap_reached
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


def test_a_draft_is_left_alone_until_it_is_ready() -> None:
    assert not gate.decide(event(draft=True)).run
    # ...and reviewed the moment it is, without waiting for another push.
    assert gate.decide(event(draft=True, action="ready_for_review", labels=[])).run


def test_a_fork_never_runs() -> None:
    # A fork's `pull_request` run has no secrets, so this would fail rather than review.
    assert not gate.decide(event(cross_repository=True)).run


# --- enrolment ---------------------------------------------------------------------


def test_a_cursor_branch_enrols_itself_at_open() -> None:
    decision = gate.decide(
        event(action="opened", labels=[], head_ref="cursor/fix-1234")
    )
    assert decision.run
    assert decision.round == 1


def test_any_other_new_branch_needs_the_opt_in_label() -> None:
    assert not gate.decide(
        event(action="opened", labels=[], head_ref="claude/some-work")
    ).run
    assert gate.decide(
        event(action="opened", labels=["loop:on"], head_ref="claude/some-work")
    ).run


def test_pull_requests_that_predate_the_loop_stay_out_of_it() -> None:
    """The guard that keeps this from firing on every PR already open.

    Enrolment happens at `opened`. A push to a long-open pull request carries no round
    label, so it is refused until someone adds `loop:on` deliberately.
    """
    decision = gate.decide(
        event(action="synchronize", labels=[], head_ref="cursor/old")
    )
    assert not decision.run
    assert "not enrolled" in decision.reason

    assert gate.decide(event(action="synchronize", labels=["loop:on"])).run


# --- asking for a round by hand -----------------------------------------------------


def test_a_collaborator_can_restart_a_parked_loop() -> None:
    decision = gate.decide(
        event(
            event_name="issue_comment",
            labels=["loop:round-1", "loop:decision"],
            is_pull_request=True,
            comment_body="Answered below — option B. @claude review please",
            comment_author_association="OWNER",
        )
    )
    assert decision.run
    assert decision.forced
    assert decision.round == 2


def test_a_comment_can_buy_a_fourth_round() -> None:
    decision = gate.decide(
        event(
            event_name="issue_comment",
            labels=["loop:round-3", "loop:done"],
            is_pull_request=True,
            comment_body="@claude review",
            comment_author_association="OWNER",
        )
    )
    assert decision.run
    assert decision.round == 4


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
        "we should ask @claude to reviewer this",
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


def test_manual_dispatch_respects_the_cap_unless_forced() -> None:
    spent = event(event_name="workflow_dispatch", labels=["loop:round-3"])
    assert not gate.decide(spent).run
    assert gate.decide(spent | {"force": True}).run


# --- the wiring the workflow depends on ---------------------------------------------


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
    assert payload.keys() == {"run", "round", "reason", "cap_reached", "forced"}
    assert payload["run"] is True
    assert payload["round"] == 2
