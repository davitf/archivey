"""The review workflow's gate, exercised without GitHub.

`scripts/review_loop_gate.py` decides whether adding the `review` label runs a round,
counts the round, and writes the comment that closes it. Everything it needs arrives as
JSON, so the cases that matter — the round cap, a person buying a round past it, a
review that died, a verdict nobody expected — are testable here instead of by labelling
a pull request and watching what happens.
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

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "review_loop_gate.py"
WORKFLOW = ROOT / ".github" / "workflows" / "review-loop.yml"

_spec = importlib.util.spec_from_file_location("review_loop_gate", SCRIPT)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
# `scripts/` is not a package, so the module is loaded by path. It has to be in
# `sys.modules` before it executes: `@dataclass` looks its own module up by name.
sys.modules[_spec.name] = gate
_spec.loader.exec_module(gate)

REPO = "davitf/archivey"
SHA = "a" * 40


def rounds(*verdicts: str, sha: str = "b" * 40) -> list[str]:
    """The first lines of closing comments for rounds that ended in these verdicts."""
    return [
        f"{gate.ROUND_MARKER} n={n} sha={sha} verdict={v} -->"
        for n, v in enumerate(verdicts, start=1)
    ]


def decide(**overrides) -> gate.Decision:
    """An agent labelling a pull request that has had no round yet."""
    base = {
        "labels": [gate.REVIEW_LABEL],
        "markers": [],
        "head_sha": SHA,
        "sender_type": "Bot",
        "sender_login": "claude[bot]",
        "label_app": "app=claude",
        "repository": REPO,
    }
    return gate.decide(base | overrides)


#: What a person clicking the label in GitHub's own interface looks like.
PERSON = {"sender_type": "User", "sender_login": "davitf", "label_app": "app="}


def finish(verdict: object, **overrides) -> gate.Finish:
    base = {
        "round": 2,
        "final": False,
        "head_sha": SHA,
        "repository": REPO,
        "verdict": verdict,
    }
    return gate.finish(base | overrides)


def verdict_file(**overrides) -> dict:
    return {
        "verdict": "findings",
        "summary": "Two nits left.",
        "question": "",
    } | overrides


# --- deciding whether a round runs ----------------------------------------------------


def test_the_label_runs_the_next_round() -> None:
    first = decide()
    assert first.run
    assert first.round == 1
    assert not first.final

    third = decide(markers=rounds("findings", "findings"))
    assert third.run
    assert third.round == 3


def test_a_label_that_has_come_off_again_runs_nothing() -> None:
    """A round that started meanwhile took the label, or a person changed their mind.

    The workflow reads the labels when the run starts, not from the event, so a request
    queued behind a round that already served it is dropped here.
    """
    answer = decide(labels=["documentation"])
    assert not answer.run
    assert not answer.comment


def test_the_last_round_an_agent_can_ask_for_says_so() -> None:
    answer = decide(markers=rounds(*["findings"] * (gate.MAX_ROUNDS - 1)))
    assert answer.run
    assert answer.round == gate.MAX_ROUNDS
    assert answer.final


def test_an_agent_cannot_ask_for_a_round_past_the_cap() -> None:
    """The cap is what stops two agents going back and forth on one pull request."""
    answer = decide(markers=rounds(*["findings"] * gate.MAX_ROUNDS))
    assert not answer.run
    assert answer.round == gate.MAX_ROUNDS
    # The refusal is announced, because nothing else on the pull request would say why
    # the label came off without a review.
    assert "needs a person" in answer.comment
    assert f"`{gate.REVIEW_LABEL}`" in answer.comment
    # It is bookkeeping, so it carries no marker and does not count as anything.
    assert "archivey-review-" not in answer.comment


@pytest.mark.parametrize(
    "who",
    [
        {"sender_type": "Bot", "label_app": "app="},
        # An agent acting through the maintainer's account: #384 recorded exactly this.
        {"sender_type": "User", "label_app": "app=claude"},
        # The event could not be found, so nobody can say it was a person.
        {"sender_type": "User", "label_app": ""},
        {"sender_type": "User", "label_app": None},
        {"sender_type": None, "label_app": "app="},
    ],
    ids=["bot", "user-via-app", "user-no-event", "user-app-none", "no-sender"],
)
def test_only_a_person_buys_a_round_past_the_cap(who: dict) -> None:
    past_cap = rounds(*["findings"] * (gate.MAX_ROUNDS + 1))
    refused = decide(markers=past_cap, **who)
    assert not refused.run
    assert not refused.person

    person = decide(markers=past_cap, **PERSON)
    assert person.run
    assert person.person
    assert person.final
    assert person.round == gate.MAX_ROUNDS + 2


def test_nothing_runs_past_the_ceiling_whoever_asks() -> None:
    """A misread person check must not remove the bound altogether."""
    answer = decide(markers=rounds(*["findings"] * gate.MAX_FORCED_ROUNDS), **PERSON)
    assert not answer.run
    assert "most this workflow runs" in answer.comment


@pytest.mark.parametrize("verdict", ["clean", "approved"])
def test_a_round_that_needs_no_other_refuses_an_agent(verdict: str) -> None:
    """The verdict is what ends the rounds, so it has to bind an agent that ignores it."""
    history = rounds("findings", verdict)
    refused = decide(markers=history)
    assert not refused.run
    assert "said no further round is needed" in refused.comment

    assert decide(markers=history, **PERSON).run


@pytest.mark.parametrize("verdict", ["findings", "decision", "not-a-verdict"])
def test_a_round_that_asked_for_more_lets_an_agent_continue(verdict: str) -> None:
    assert decide(markers=rounds(verdict)).run


def test_the_latest_round_is_the_one_whose_verdict_counts() -> None:
    """A person's round after an approval can reopen the rounds for the agent."""
    # Listed out of order: comments arrive paginated, and the round number decides.
    history = list(reversed(rounds("approved", "findings")))
    assert decide(markers=history).run


def test_an_agent_cannot_retry_a_commit_a_review_already_failed_on() -> None:
    """A failure that repeats every time would otherwise loop without end.

    `claude-code-action` skips when the branch's copy of the workflow differs from
    `main`'s, so retrying the same commit fails the same way, and each closing comment
    asks for another retry.
    """
    failed = [f"{gate.ATTEMPT_MARKER} n=1 sha={SHA} -->"]
    refused = decide(markers=failed)
    assert not refused.run
    assert "merge `main`" in refused.comment

    # A new commit, or a person, retries.
    assert decide(markers=failed, head_sha="c" * 40).run
    assert decide(markers=failed, **PERSON).run
    # A failed attempt is not a round.
    assert decide(markers=failed, head_sha="c" * 40).round == 1


def test_no_review_keeps_a_pull_request_out_whoever_asks() -> None:
    """The review hub's diff is the whole repository; one label must not review it."""
    labels = [gate.REVIEW_LABEL, gate.NO_REVIEW_LABEL]
    for who in [{}, PERSON]:
        answer = decide(labels=labels, **who)
        assert not answer.run
        assert f"`{gate.NO_REVIEW_LABEL}`" in answer.comment


@pytest.mark.parametrize("value", [None, "", "three", 4, [1, None, "<!-- other -->"]])
def test_markers_that_cannot_be_read_count_for_nothing(value: object) -> None:
    assert decide(markers=value).round == 1


# --- the verdict a round ends with ---------------------------------------------------


def test_a_review_that_does_not_ask_to_see_the_fix_ends_the_rounds() -> None:
    assert gate.read_verdict(verdict_file(verdict="clean")).stop
    assert gate.read_verdict(verdict_file(verdict="approved")).stop
    assert not gate.read_verdict(verdict_file(verdict="findings")).stop
    assert not gate.read_verdict(verdict_file(verdict="decision")).stop


def test_a_verdict_nobody_expected_still_gets_an_answer() -> None:
    """The verdict is prose an agent wrote, so nothing may fall through silently."""
    for payload in ["approve", "APPROVED?", "", None, 3]:
        answer = gate.read_verdict({"verdict": payload})
        assert answer.verdict == gate.DEFAULT_VERDICT, payload
        assert not answer.stop, payload
        assert "not one this loop knows" in answer.reason, payload

    for payload in [[], "unreadable", None]:
        answer = gate.read_verdict(payload)
        assert answer.verdict == gate.DEFAULT_VERDICT
        assert "not a JSON object" in answer.reason

    # Spelling and spacing are the reviewer's, not a contract.
    assert gate.read_verdict({"verdict": "  Approved\n"}).verdict == "approved"


def test_a_question_belongs_to_a_decision_and_to_nothing_else() -> None:
    assert gate.read_verdict(verdict_file(verdict="decision", question="Q?")).question
    for name in ["clean", "approved", "findings"]:
        assert not gate.read_verdict(verdict_file(verdict=name, question="Q?")).question


# --- the comment that closes a round ---------------------------------------------------


def test_a_review_that_did_not_finish_is_not_counted() -> None:
    """No verdict file means the review died, and a round that died is free to retry."""
    answer = finish(None)
    assert not answer.counted
    assert gate.ROUND_MARKER not in answer.comment
    assert "did not finish" in answer.comment
    # It records the commit, which is what stops an agent retrying it forever.
    first_line = answer.comment.splitlines()[0]
    assert first_line == f"{gate.ATTEMPT_MARKER} n=2 sha={SHA} -->"
    assert gate.read_history([first_line]).failed_shas == {SHA}
    assert f"`{gate.REVIEW_LABEL}`" in answer.comment


@pytest.mark.parametrize("name", ["clean", "approved", "findings", "decision"])
def test_every_finished_round_counts_and_records_what_it_read(name: str) -> None:
    answer = finish(verdict_file(verdict=name, question="Fix here or file it?"))
    assert answer.counted
    assert answer.verdict == name
    # The marker is the first line, because the workflow counts `startswith`.
    first_line = answer.comment.splitlines()[0]
    assert first_line == f"{gate.ROUND_MARKER} n=2 sha={SHA} verdict={name} -->"
    # What the gate reads back is what this wrote.
    history = gate.read_history([first_line])
    assert (history.rounds, history.last_verdict) == (1, name)
    assert "Two nits left." in answer.comment
    assert "https://github.com/davitf/archivey/blob/main/dev-docs/review-loop.md" in (
        answer.comment
    )


def test_a_garbled_verdict_file_still_counts() -> None:
    """The findings were posted; only the verdict could not be read."""
    answer = finish("unreadable")
    assert answer.counted
    assert answer.verdict == gate.DEFAULT_VERDICT
    assert "round 3" in answer.comment


def test_findings_ask_for_the_label_back_for_the_next_round() -> None:
    answer = finish(verdict_file(verdict="findings"))
    assert not answer.stop
    assert "add the `review` label again for round 3" in answer.comment


def test_the_last_round_does_not_ask_an_agent_for_another() -> None:
    """A comment that asks for a round the cap will refuse wakes a runner to say no."""
    answer = finish(verdict_file(verdict="findings"), round=5, final=True)
    assert "again for round" not in answer.comment
    assert "unless a person adds" in answer.comment


def test_a_round_that_does_not_need_another_look_says_so() -> None:
    approved = finish(verdict_file(verdict="approved"))
    assert approved.stop
    assert "No further round is needed" in approved.comment
    assert "again for round" not in approved.comment

    clean = finish(verdict_file(verdict="clean"))
    assert "ready for a person to look at and merge" in clean.comment


def test_a_decision_carries_its_question() -> None:
    answer = finish(verdict_file(verdict="decision", question="Fix here or file it?"))
    assert "**The question:** Fix here or file it?" in answer.comment
    assert "Once it is answered, add the `review` label again" in answer.comment


# --- the command line the workflow calls ----------------------------------------------


def _run(stdin: str, *args: str) -> dict:
    result = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_decide_mode_writes_the_keys_the_workflow_reads() -> None:
    payload = _run(json.dumps({"labels": ["review"], "markers": []}))
    for key in ["run", "round", "final", "person", "max_rounds", "comment"]:
        assert key in payload, key
    assert payload["run"] is True


def test_finish_mode_writes_the_keys_the_workflow_reads() -> None:
    payload = _run(json.dumps({"round": 1, "verdict": verdict_file()}), "--finish")
    assert payload.keys() == {"verdict", "stop", "counted", "comment", "reason"}


def test_neither_mode_fails_on_input_it_cannot_parse() -> None:
    """By the time `--finish` runs, the findings are on the pull request already."""
    assert _run("{not json", "--finish")["comment"] == ""
    assert _run("{not json")["run"] is False


# --- what the workflow has to keep in step with the gate ------------------------------


def test_the_workflow_counts_the_marker_the_gate_writes() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    prefix = "<!-- archivey-review-"
    assert gate.ROUND_MARKER.startswith(prefix)
    assert gate.ATTEMPT_MARKER.startswith(prefix)
    assert f'startswith("{prefix}")' in text
    # Only the workflow's own comments count, so quoting the marker changes nothing.
    assert 'select(.user.login == "github-actions[bot]")' in text


def test_the_workflow_listens_for_the_gate_s_label() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert f"github.event.label.name == '{gate.REVIEW_LABEL}'" in text
    assert f"/labels/{gate.REVIEW_LABEL}" in text


def test_the_label_comes_off_before_the_review_runs() -> None:
    """A request made during a round must queue a new round, not be deleted by this one.

    Adding a label that is already present raises no event, so the label has to be off
    for the next request to exist at all — and taking it off at the end would delete a
    request made while the review ran.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.index("- name: Take the label off") < text.index(
        "- name: Claude review"
    )
    step = text[text.index("- name: Take the label off") :]
    assert step.splitlines()[1].strip() == "if: always()"


#: `run: |` blocks in the workflow are indented ten spaces.
_BLOCK_INDENT = " " * 10


def _close_step() -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if "- name: Close the round" in line)
    body = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    out = []
    for line in lines[body + 1 :]:
        if line.strip() and not line.startswith(_BLOCK_INDENT):
            break
        out.append(line[len(_BLOCK_INDENT) :])
    return "\n".join(out)


# The step runs only on `ubuntu-latest`. Under Git Bash on Windows the substituted
# paths are backslashed and unquoted in the step, so bash would read a different path
# than the one the test wrote — a failure of the harness, not of the step.
@pytest.mark.skipif(sys.platform == "win32", reason="the step runs on ubuntu-latest")
@pytest.mark.parametrize(
    ("verdict_text", "counted"),
    [
        (None, False),
        (json.dumps(verdict_file(verdict="approved")), True),
        ("this is not json", True),
        ("[1, 2]", True),
    ],
    ids=["missing", "approved", "garbled", "not-an-object"],
)
def test_the_close_step_runs_as_written(
    tmp_path: Path, verdict_text: str | None, counted: bool
) -> None:
    """Run the step's own shell, with `gh` stubbed, against each shape of verdict file."""
    bash, jq = shutil.which("bash"), shutil.which("jq")
    if bash is None or jq is None:  # pragma: no cover - minimal runners
        pytest.skip("needs bash and jq")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/gh.log\n', encoding="utf-8")
    stub.chmod(0o755)

    work = tmp_path / "work"
    work.mkdir()
    if verdict_text is not None:
        (work / "loop-verdict.json").write_text(verdict_text, encoding="utf-8")

    script = _close_step().replace("/tmp/", f"{work}/")
    env = os.environ | {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "ROUND": "2",
        "FINAL": "false",
        "HEAD_SHA": SHA,
        "PR": "7",
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }
    subprocess.run([bash, "-c", script], cwd=ROOT, env=env, check=True)  # noqa: S603

    result = json.loads((work / "finish.json").read_text(encoding="utf-8"))
    assert result["counted"] is counted
    assert "pr comment 7" in (tmp_path / "gh.log").read_text(encoding="utf-8")
    posted = (work / "comment.md").read_text(encoding="utf-8")
    assert posted.startswith(gate.ROUND_MARKER) is counted
