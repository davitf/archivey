"""The review hub watchdog's decision, exercised without GitHub.

`scripts/review_hub_watchdog_gate.py` decides whether the hub needs reopening and, if it
does, which surface closed it. Everything it needs arrives as JSON, so the branch that
only ever runs during an actual incident is testable here instead of by closing the
review hub and watching what happens.

That is the whole reason this file exists. The watchdog reads `OPEN` and exits on every
push to `main`; every line below that runs only on the one event the workflow is for, so
a break in it stays invisible until the moment it is needed. The first version shipped
with exactly such a break, and the case that would have caught it — a timeline served
over several pages, whose last `closed` event is not the last one on any single page — is
`test_a_body_closure_is_read_from_the_last_closed_event` below.

Every test here names the mutation it was shown to fail against, per CONTRIBUTING
§Testing standards.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_hub_watchdog_gate.py"

_spec = importlib.util.spec_from_file_location("review_hub_watchdog_gate", SCRIPT)
assert _spec is not None and _spec.loader is not None
watchdog = importlib.util.module_from_spec(_spec)
# `scripts/` is not a package, so the module is loaded by path, the way
# `test_review_loop_gate.py` loads its own.
sys.modules[_spec.name] = watchdog
_spec.loader.exec_module(watchdog)

# The hub's three real closures, oldest first, as the issues-API timeline serves them.
# `7a8cbb00` (2026-09-11) and `0454c54` (2026-09-21) came from commit messages; the third
# came from a merged pull request's body and carries no commit at all.
FIRST_CLOSE = {
    "event": "closed",
    "commit_id": "7a8cbb000d8b8c97618e280a0f94581cf1c4b682",
}
SECOND_CLOSE = {
    "event": "closed",
    "commit_id": "0454c54081d2c1147a8c5829f468f7bbf535f028",
}
THIRD_CLOSE = {"event": "closed", "commit_id": None}

# `referenced` events carry a `commit_id` and are not closures. The hub's timeline holds
# hundreds of them, so a filter that forgets to select on `event` reads one of these.
A_REFERENCE = {
    "event": "referenced",
    "commit_id": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
}


def test_an_open_hub_is_left_alone() -> None:
    """Mutation: drop the `state == "OPEN"` arm. Then every push to `main` reopens
    an already-open pull request."""
    answer = watchdog.decide({"state": "OPEN", "labels": [], "timeline": [THIRD_CLOSE]})
    assert answer["action"] == "none"


def test_a_merged_hub_is_an_error_rather_than_a_reopen() -> None:
    """Mutation: make `MERGED` fall through to the reopen path. Then the job reports
    success while `gh pr reopen` refuses, and the threads are stranded silently."""
    answer = watchdog.decide(
        {"state": "MERGED", "labels": [], "timeline": [SECOND_CLOSE]}
    )
    assert answer["action"] == "fail"


def test_the_deliberate_label_stops_the_reopen() -> None:
    """Mutation: drop the label arm. Then the watchdog reopens the hub under a
    maintainer who closed it on purpose, every six hours."""
    answer = watchdog.decide(
        {
            "state": "CLOSED",
            "labels": ["loop:off", watchdog.DELIBERATE_LABEL],
            "timeline": [THIRD_CLOSE],
        }
    )
    assert answer["action"] == "none"
    assert watchdog.DELIBERATE_LABEL in answer["reason"]


def test_a_body_closure_is_read_from_the_last_closed_event() -> None:
    """The regression this file exists for.

    Mutation: take `closes[0]` instead of `closes[-1]`. The answer becomes the 2026-09-11
    commit closure, which is what the shipped `--paginate`/`jq last` filter reported for
    a closure that carried no commit at all.
    """
    answer = watchdog.decide(
        {
            "state": "CLOSED",
            "labels": [],
            "timeline": [FIRST_CLOSE, A_REFERENCE, SECOND_CLOSE, THIRD_CLOSE],
        }
    )
    assert answer["action"] == "reopen"
    assert answer["surface"] == "body"
    assert answer["commit"] is None


def test_a_commit_closure_names_its_commit() -> None:
    """Mutation: return `surface="body"` unconditionally. Then the reopen comment
    never names the commit that closed the hub, which is the diagnostic."""
    answer = watchdog.decide(
        {"state": "CLOSED", "labels": [], "timeline": [THIRD_CLOSE, SECOND_CLOSE]}
    )
    assert answer["action"] == "reopen"
    assert answer["surface"] == "commit"
    assert answer["commit"] == SECOND_CLOSE["commit_id"]


def test_a_reference_event_is_not_a_closure() -> None:
    """Mutation: drop `select(.event == "closed")`, i.e. the `e.get("event")` test.
    Then the newest `referenced` event — of which the hub has hundreds — is reported
    as the commit that closed it."""
    answer = watchdog.decide(
        {"state": "CLOSED", "labels": [], "timeline": [SECOND_CLOSE, A_REFERENCE]}
    )
    assert answer["surface"] == "commit"
    assert answer["commit"] == SECOND_CLOSE["commit_id"]


def test_an_empty_commit_id_reads_as_a_body_closure() -> None:
    """Mutation: test `is None` instead of falsiness. GitHub serving `""` rather than
    omitting the field would then produce `closed by a commit message, ``."""
    answer = watchdog.decide(
        {
            "state": "CLOSED",
            "labels": [],
            "timeline": [{"event": "closed", "commit_id": ""}],
        }
    )
    assert answer["surface"] == "body"
    assert answer["commit"] is None


def test_a_closed_hub_with_no_closed_event_says_so() -> None:
    """Mutation: return `surface="body"` for an empty list. Then a truncated or
    unreadable timeline is reported as a body closure, which is a guess stated as
    a fact — the failure mode round 1 of this pull request was about."""
    answer = watchdog.decide({"state": "CLOSED", "labels": [], "timeline": []})
    assert answer["action"] == "reopen"
    assert answer["surface"] == "unknown"


def test_missing_keys_do_not_crash_the_job() -> None:
    """Mutation: index `facts["labels"]` directly. A timeline fetch that failed and
    left the key out would then abort the step before the reopen, which is the one
    action that must happen whatever else is unknown."""
    answer = watchdog.decide({"state": "CLOSED"})
    assert answer["action"] == "reopen"


def test_the_script_runs_as_a_filter() -> None:
    """Mutation: write the answer to stderr, or forget the trailing newline. The
    workflow reads stdout with `jq`, so either leaves it parsing nothing."""
    facts = {"state": "CLOSED", "labels": [], "timeline": [SECOND_CLOSE]}
    out = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(facts),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert json.loads(out)["commit"] == SECOND_CLOSE["commit_id"]
