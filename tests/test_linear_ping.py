"""The Linear delivery hop for the review loop's findings ping, exercised offline.

`scripts/linear_ping.py` is what stops a Cursor pull request sitting at `loop:round-1`
forever because the implementer never learned there were findings. Two things about it
are worth pinning, and neither needs the network:

1. **It finds the issue in a real Cursor pull request body.** The identifier is the
   loop's only link back to the delegation that started the work, and it is recovered
   by parsing rather than stored, so a change in how Cursor writes that line breaks the
   hop quietly.
2. **Every way it can fail ends in exit 0 with something said.** The findings are
   already on the pull request by the time this runs. A non-zero exit here would mark
   the round failed and, through `loop:hold`, stop the loop over a convenience hop —
   which is a worse outcome than the bug this script fixes. Silence would be worse
   still, since silence is exactly how the original break hid.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "linear_ping.py"

_spec = importlib.util.spec_from_file_location("linear_ping", SCRIPT)
assert _spec is not None and _spec.loader is not None
ping = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ping
_spec.loader.exec_module(ping)


# The shape Cursor writes into every pull request body it opens.
CURSOR_BODY = """\
<!-- CURSOR_AGENT_PR_BODY_BEGIN -->
`ConcatenatedFile` used to open every Path volume at construction.
<!-- CURSOR_AGENT_PR_BODY_END -->

Linear Issue: [TEAM-123](https://linear.app/archivey/issue/TEAM-123/a-slug-that-moves)
"""


def test_finds_the_issue_in_a_real_cursor_body():
    assert ping.find_issue(CURSOR_BODY) == ("TEAM", 123)


def test_ignores_the_slug_so_a_renamed_issue_still_resolves():
    """The link text is the identifier; the URL slug is a title and moves."""
    body = "Linear Issue: [TEAM-9](https://linear.app/archivey/issue/TEAM-9/renamed)"
    assert ping.find_issue(body) == ("TEAM", 9)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "no issue here",
        # A bare URL with no `Linear Issue:` line is not the shape Cursor writes, and
        # guessing from any linear.app link in the body would pick up an issue someone
        # merely referenced in prose.
        "see https://linear.app/archivey/issue/TEAM-123/something",
    ],
)
def test_no_issue_is_not_an_error(body):
    assert ping.find_issue(body) is None


def _run(tmp_path, monkeypatch, pr_body, *, key=None):
    pr_file = tmp_path / "pr.md"
    pr_file.write_text(pr_body, encoding="utf-8")
    body_file = tmp_path / "ping.md"
    body_file.write_text("@cursor please address the findings", encoding="utf-8")

    if key is None:
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    else:
        monkeypatch.setenv("LINEAR_API_KEY", key)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "linear_ping.py",
            "--pr-body-file",
            str(pr_file),
            "--body-file",
            str(body_file),
        ],
    )
    return ping.main()


def test_a_pr_with_no_linear_issue_exits_clean_and_says_so(
    tmp_path, monkeypatch, capsys
):
    assert _run(tmp_path, monkeypatch, "no issue here", key="lin_api_x") == 0
    assert "names no Linear issue" in capsys.readouterr().err


def test_a_missing_api_key_exits_clean_and_names_the_issue(
    tmp_path, monkeypatch, capsys
):
    assert _run(tmp_path, monkeypatch, CURSOR_BODY) == 0
    err = capsys.readouterr().err
    assert "LINEAR_API_KEY is not set" in err
    # Naming the issue is what makes the warning actionable: a person reading the run
    # can post the comment by hand, which is the documented workaround.
    assert "TEAM-123" in err


def test_an_unreachable_api_exits_clean_and_says_so(tmp_path, monkeypatch, capsys):
    """A Linear outage must not fail the round: the findings are already posted."""

    def explode(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(ping.urllib.request, "urlopen", explode)
    assert _run(tmp_path, monkeypatch, CURSOR_BODY, key="lin_api_x") == 0
    assert "could not reach the API" in capsys.readouterr().err


def test_the_warning_also_lands_in_the_job_summary(tmp_path, monkeypatch):
    """stderr alone is a warning nobody reads; the summary is what a person opens."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert _run(tmp_path, monkeypatch, CURSOR_BODY) == 0
    assert "LINEAR_API_KEY is not set" in summary.read_text(encoding="utf-8")
