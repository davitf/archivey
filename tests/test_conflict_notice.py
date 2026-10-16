"""The conflict notice's decisions, exercised without GitHub.

`scripts/conflict_notice.py` decides, for one open pull request, whether to post a
conflict notice and whether to take the `review` label off. A mistake there is either
silence (the stall it exists to end) or a comment every hour, so the cases are pinned
here rather than found on a live pull request.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "conflict_notice.py"

_spec = importlib.util.spec_from_file_location("conflict_notice", SCRIPT)
assert _spec is not None and _spec.loader is not None
notice = importlib.util.module_from_spec(_spec)
# `@dataclass` looks its own module up by name, so register it before executing.
sys.modules[_spec.name] = notice
_spec.loader.exec_module(notice)

REPO = "davitf/archivey"
HEAD = "a" * 40
NOW = datetime(2026, 10, 16, 13, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(hours=2)
JUST_NOW = NOW - timedelta(minutes=1)


def pr(mergeable: bool | None, *labels: str, head: str = HEAD) -> dict:
    return {
        "mergeable": mergeable,
        "head": {"sha": head},
        "labels": [{"name": name} for name in labels],
    }


def decide(p: dict, bodies: list[str] | None = None, added=LONG_AGO):
    return notice.decide(p, bodies or [], added, NOW, REPO)


def test_mergeable_or_unknown_does_nothing() -> None:
    assert decide(pr(True, "review")) == notice.Action()
    assert decide(pr(None, "review")) == notice.Action()


def test_conflict_is_announced_once_per_head() -> None:
    first = decide(pr(False))
    assert first.comment.startswith(notice.marker(HEAD) + "\n")
    assert not first.remove_label
    assert "review-loop.md" in first.comment

    assert decide(pr(False), [first.comment]) == notice.Action()
    # A new head that conflicts again gets its own notice.
    later = decide(pr(False, head="b" * 40), [first.comment])
    assert later.comment.startswith(notice.marker("b" * 40))


def test_label_comes_off_with_the_first_notice() -> None:
    action = decide(pr(False, "review"))
    assert action.remove_label
    assert notice.marker(HEAD) in action.comment
    assert notice.LABEL_NOTE in action.comment


def test_label_added_after_the_notice_still_comes_off_and_says_so() -> None:
    # The notice at this head is already posted; someone then added the label.
    posted = decide(pr(False)).comment
    action = decide(pr(False, "review"), [posted])
    assert action.remove_label
    assert action.comment.startswith(notice.LABEL_NOTE)
    assert notice.marker(HEAD) not in action.comment


def test_a_label_just_added_is_left_for_its_queued_round() -> None:
    action = decide(pr(False, "review"), added=JUST_NOW)
    assert not action.remove_label
    assert notice.LABEL_NOTE not in action.comment
    # Already announced: nothing at all until the grace period has passed.
    posted = action.comment
    assert decide(pr(False, "review"), [posted], added=JUST_NOW) == notice.Action()
    assert decide(pr(False, "review"), [posted]).remove_label


def test_unknown_label_time_counts_as_old() -> None:
    assert decide(pr(False, "review"), added=None).remove_label


def test_marker_only_counts_at_the_start_of_a_comment() -> None:
    quoted = f"As the notice said:\n> {notice.marker(HEAD)}"
    assert decide(pr(False), [quoted]).comment.startswith(notice.marker(HEAD))
