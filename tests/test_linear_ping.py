"""The Linear delivery hop for the review loop's findings ping, exercised offline.

`scripts/linear_ping.py` is what stops a Cursor pull request sitting at `loop:round-1`
forever because the implementer never learned there were findings. Two things about it
are worth pinning, and neither needs the network:

1. **It finds the issue.** The primary route asks Linear which issue holds this pull
   request as an attachment; the body footer is a fallback for pull requests opened
   before that footer was dropped. Both are recovered rather than stored, so a change
   at either end breaks the hop quietly. The body parsing is pinned here; the
   attachment query needs the network and is checked by the `Linear ping check`
   workflow instead.
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


# The shape Cursor used to write into every pull request body it opened, until the
# maintainer ruled the footer off on 2026-09-20. Pull requests opened before then
# still carry it, which is the only reason the fallback exists.
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


PR_URL = "https://github.com/davitf/archivey/pull/999"


def _run(tmp_path, monkeypatch, pr_body, *, key=None):
    pr_file = tmp_path / "pr.md"
    pr_file.write_text(pr_body, encoding="utf-8")
    body_file = tmp_path / "ping.md"
    body_file.write_text("@cursoragent please address the findings", encoding="utf-8")

    if key is None:
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    else:
        monkeypatch.setenv("LINEAR_API_KEY", key)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "linear_ping.py",
            "--pr-url",
            PR_URL,
            "--pr-body-file",
            str(pr_file),
            "--body-file",
            str(body_file),
        ],
    )
    return ping.main()


def _attached(*issues):
    """The shape `attachmentsForURL` returns."""
    return {"attachmentsForURL": {"nodes": [{"issue": i} for i in issues]}}


def test_a_pr_no_issue_can_be_found_for_exits_clean_and_says_so(
    tmp_path, monkeypatch, capsys
):
    """Neither route finds anything: nothing attached, and no footer in the body."""
    monkeypatch.setattr(ping, "call", lambda *_a, **_k: _attached())
    assert _run(tmp_path, monkeypatch, "no issue here", key="lin_api_x") == 0
    err = capsys.readouterr().err
    assert "no Linear issue holds" in err
    assert PR_URL in err
    assert "TEAM" not in err


def test_the_attachment_route_is_tried_before_the_body(tmp_path, monkeypatch):
    """The body footer is the fallback, so a PR that still has one must not be read
    when Linear already knows which issue the pull request belongs to."""
    seen = []

    def fake_call(query, variables, _key):
        seen.append(variables)
        if "IssueByAttachmentUrl" in query:
            return _attached({"id": "uuid-1", "identifier": "TEAM-1"})
        if "IssueByNumber" in query:
            raise AssertionError("the body route must not be reached")
        return {"commentCreate": {"success": True}}

    monkeypatch.setattr(ping, "call", fake_call)
    assert _run(tmp_path, monkeypatch, CURSOR_BODY, key="lin_api_x") == 0
    assert seen[0] == {"url": PR_URL}
    assert seen[1]["issueId"] == "uuid-1"  # posted straight to the attached issue


@pytest.mark.parametrize(
    "data",
    [
        {"attachmentsForURL": None},
        {"attachmentsForURL": {"nodes": None}},
        {"attachmentsForURL": {"nodes": [{"issue": None}]}},
        {"attachmentsForURL": {"nodes": [{"issue": {"id": "uuid-1"}}]}},
        {},
        None,
    ],
)
def test_an_odd_shaped_answer_reads_as_nothing_found(data):
    """A null field is a shape Linear can return, and it used to be an `AttributeError`
    that killed the step before the loop's status comment was written — labels already
    flipped, nothing on the pull request saying so. Every one of these is "no issue"."""
    assert ping._attached_issues(data) == []


def test_an_unexpected_failure_anywhere_still_exits_clean(
    tmp_path, monkeypatch, capsys
):
    """The catch-all behind the module's promise, exercised through a route that has
    no specific handler: `call` itself raising rather than returning None."""

    def explode(*_args, **_kwargs):
        raise RuntimeError("something nobody enumerated")

    monkeypatch.setattr(ping, "call", explode)
    assert _run(tmp_path, monkeypatch, CURSOR_BODY, key="lin_api_x") == 0
    assert "unexpected RuntimeError" in capsys.readouterr().err


def test_the_attachment_is_asked_for_exactly_once(tmp_path, monkeypatch):
    """A query that worked and found nothing is an answer, not a failure."""
    tried = []

    def fake_call(query, _variables, _key):
        if "IssueByAttachment" in query:
            tried.append(query)
        return _attached()

    monkeypatch.setattr(ping, "call", fake_call)
    assert _run(tmp_path, monkeypatch, "no footer here", key="lin_api_x") == 0
    assert len(tried) == 1


def test_the_body_footer_still_works_when_nothing_is_attached(tmp_path, monkeypatch):
    """Pull requests opened before the footer was dropped must keep resolving."""
    posted = {}

    def fake_call(query, variables, _key):
        if "IssueByAttachment" in query:
            return _attached()
        if "IssueByNumber" in query:
            assert variables == {"team": "TEAM", "number": 123.0}
            return {"issues": {"nodes": [{"id": "uuid-2", "identifier": "TEAM-123"}]}}
        posted.update(variables)
        return {"commentCreate": {"success": True}}

    monkeypatch.setattr(ping, "call", fake_call)
    assert _run(tmp_path, monkeypatch, CURSOR_BODY, key="lin_api_x") == 0
    assert posted["issueId"] == "uuid-2"


def test_a_missing_api_key_exits_clean_without_naming_the_issue(
    tmp_path, monkeypatch, capsys
):
    """The warning has to be actionable without being a leak.

    It used to name the issue out of the body footer, so a person could post the
    comment by hand. This repository is public, so Actions logs and job summaries are
    public, and a tracker key in one is the same leak the footer was removed for. The
    warning now points at the attachment instead, which is on Linear.
    """
    assert _run(tmp_path, monkeypatch, CURSOR_BODY) == 0
    err = capsys.readouterr().err
    assert "LINEAR_API_KEY is not set" in err
    assert "attached" in err
    assert "TEAM-123" not in err


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


def test_a_dry_run_reports_through_the_job_summary(tmp_path, monkeypatch):
    """`Linear ping check` tells a person to read the job summary, and for the one
    path it actually runs — a successful dry run — nothing used to get there. A blank
    summary and a green job are indistinguishable from a credential that works."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(
        ping, "call", lambda *_a, **_k: _attached({"id": "u", "identifier": "TEAM-7"})
    )

    pr_file = tmp_path / "pr.md"
    pr_file.write_text("no footer here", encoding="utf-8")
    body_file = tmp_path / "ping.md"
    body_file.write_text("please address the findings", encoding="utf-8")
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_x")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "linear_ping.py",
            "--pr-url",
            PR_URL,
            "--pr-body-file",
            str(pr_file),
            "--body-file",
            str(body_file),
            "--dry-run",
        ],
    )

    assert ping.main() == 0
    written = summary.read_text(encoding="utf-8")
    assert "resolved the issue" in written
    assert "please address the findings" in written
    assert "TEAM-7" not in written


def test_a_delivery_reports_through_the_job_summary(tmp_path, monkeypatch):
    """Same reason as the dry run: a success nobody can see proves nothing."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    def fake_call(query, _variables, _key):
        if "IssueByAttachment" in query:
            return _attached({"id": "u", "identifier": "TEAM-7"})
        return {"commentCreate": {"success": True}}

    monkeypatch.setattr(ping, "call", fake_call)
    assert _run(tmp_path, monkeypatch, "no footer here", key="lin_api_x") == 0
    written = summary.read_text(encoding="utf-8")
    assert "delivered" in written
    assert "TEAM-7" not in written
