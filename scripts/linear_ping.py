"""Deliver the review loop's findings ping to Cursor, through Linear.

`dev-docs/review-loop.md` listed this as an unobserved rough edge: the workflow posts
the findings ping with `GITHUB_TOKEN`, so it arrives from `github-actions[bot]`, and
nobody had checked whether a Cursor background agent whose session has already ended
wakes up for it. Measured on #374 (2026-09-20):

    00:14  Cursor's agent reports finished; its session ends.
    00:21  the workflow posts the ping on the pull request.
    ------ 66 minutes. No push, no comment, no new agent.
    01:27  the same ask, posted as a `@cursor` comment on the Linear issue.
    01:41  Cursor pushes all seven findings fixed, and asks for the next round.

Fourteen minutes against sixty-six of silence, and slowness is not the explanation —
the same agent went from Linear delegation to a finished pull request in about seven
minutes earlier that night.

**What that measurement does not prove.** The GitHub ping said `@cursor`, and there is
no GitHub user by that name: the app posts as `cursor[bot]`, a bot login cannot be
mentioned, and the account that can is `cursoragent`. So the comment mentioned nobody,
and the silence has a simpler explanation than "a bot comment does not wake Cursor".
Contrast, same repository, 2026-09-20 03:46:35 — a human comment reading
`@cursoragent please review` on #372 got "Taking a look!" from `cursor[bot]` seven
seconds later. The handle is fixed in the workflow now.

Which leaves one cell of the table untested: the right handle, from a bot account. That
is why this hop stays — it is cheap, it cannot fail the run, and posting on the issue
is the one route measured to wake an agent whose session had ended. It is insurance on
an open question, not the fix; the handle was the fix.

**It also makes that question harder to close, and there is no way around it here.** The
GitHub comment and this hop run seconds apart in the same workflow step, so a Cursor
agent that wakes proves only that one of the two reached it. Settling which would take a
round run with this hop deliberately off, and until someone chooses to spend a round
that way, "the corrected handle is enough on its own" stays unproven rather than
disproven. Not stalling a pull request is worth more than the measurement today
(Cursor, C8 on #379); when the loop has run cleanly for a while, the experiment is one
round and the hop either goes or has earned its place.

This break is worse than it sounds, because the thirty-minute quiet period cannot
rescue it: the branch is quiet precisely because the implementer never learned there
was anything to do, so the pull request sits at `loop:round-1` until a person notices.

The GitHub comment is still posted. It is the record a human reads on the pull request,
and on a `claude/*` branch it is also the delivery mechanism — a Claude Code session
subscribed to the pull request's activity does see it. This script is the extra hop for
`cursor/*` branches only.

**Finding the issue.** The issue is the one holding this pull request as an
*attachment*, looked up by the pull request's own URL. That attachment has to be
created deliberately — `.claude/skills/address-linear-issue/SKILL.md` §2 tells the
agent to attach the pull request to the issue as soon as it exists, which is one API
call. It is **not** enough to have posted a Linear comment containing the URL: Linear's
GitHub integration links a pull request from the branch name, the title, or the
description, and none of those may carry a tracker key here, the repository being
public. That was the flaw in this change's first draft (Cursor, C1 on #379), which
assumed the comment was sufficient.

The pull request body is a fallback for bodies opened before 2026-09-20, when Cursor
appended a `Linear Issue: [KEY](url)` footer to every one — the very thing that made
Linear link them, and also a private tracker link published on a public repository,
which `AGENTS.md` forbids. The maintainer ruled the footer goes. Nothing here depends
on it coming back.

A third route was considered and not taken: finding the issue by a Linear comment whose
body contains the pull request URL. It would cover an agent that forgets to attach, but
it costs a query on every round, the filter field is unverified, and a missed
attachment is already loud rather than silent.

**Nothing here is allowed to break the loop.** A pull request no issue can be found for,
a missing API key, or a Linear outage all end in a warning and exit 0: the findings are
already posted on the pull request, and failing the run would lose that for a hop that
is a convenience. The one thing this must not do is fail *silently*, so every skip and
every error says which one it was, on stderr and in the job summary.

Usage, from the loop workflow:

    scripts/linear_ping.py --pr-url https://github.com/o/r/pull/1 --body-file ping.md

`--dry-run` resolves the issue against the live API and reports what it would post
without posting it, which is how to check the credential without spending a round.
`LINEAR_API_KEY` comes from the environment.

**Nothing printed here names a tracker issue.** This repository is public, so its
Actions logs and job summaries are public too, and an issue key in them is the same
leak the pull request footer was removed for. The output says whether an issue was
found and whether the comment landed; which issue it was is readable on Linear, from
the attachment the lookup just used.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

LINEAR_API = "https://api.linear.app/graphql"

#: A legacy footer. Cursor used to append this line to every pull request body it
#: opened, until the maintainer ruled it off on 2026-09-20 for publishing a private
#: tracker link on a public repository:
#:
#:     Linear Issue: [TEAM-123](https://linear.app/archivey/issue/TEAM-123/…)
#:
#: Pull requests opened before then still carry it and this still reads them; new ones
#: do not, and are found by attachment instead.
#:
#: Matched on the identifier in the link text rather than on the URL, because the URL
#: carries a slug that changes when the issue is renamed while the identifier does not.
ISSUE_RE = re.compile(r"Linear\s+Issue:\s*\[([A-Z][A-Z0-9]*)-(\d+)\]", re.IGNORECASE)

ISSUE_QUERY = """
query IssueByNumber($team: String!, $number: Float!) {
  issues(filter: { team: { key: { eq: $team } }, number: { eq: $number } }, first: 1) {
    nodes { id identifier }
  }
}
"""

#: The primary lookup: the issue this pull request is attached to. `attachmentsForURL`
#: is Linear's purpose-built "what is this link attached to" query, and the replacement
#: named in the deprecation of `attachmentIssue`. Filtering `issues` by
#: `attachments.url` asks the same question the other way round and is equally valid;
#: this shipped both while the schema was unverified, and keeps only this one now that
#: Cursor checked it against `schema.graphql` (C4 on #379).
#:
#: ``first: 2`` rather than 1 on purpose: two issues claiming one pull request is a
#: state worth naming in the log rather than silently picking the first of.
ATTACHMENT_QUERY = """
query IssueByAttachmentUrl($url: String!) {
  attachmentsForURL(url: $url, first: 2) {
    nodes { issue { id identifier } }
  }
}
"""

COMMENT_MUTATION = """
mutation AddComment($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
    comment { id }
  }
}
"""


def find_issue(pr_body: str) -> tuple[str, int] | None:
    """Return the ``(team key, number)`` of the Linear issue a PR body names."""
    match = ISSUE_RE.search(pr_body)
    if match is None:
        return None
    return match.group(1).upper(), int(match.group(2))


def note(message: str) -> None:
    """Say something that must not be missed, on stderr and in the job summary.

    A skip that only prints to stderr is a skip nobody reads: the loop's whole failure
    mode here was a delivery hop going quiet. The job summary is what a person opening
    the run actually sees.
    """
    print(message, file=sys.stderr)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    try:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"{message}\n\n")
    except OSError as exc:  # pragma: no cover - the summary is a convenience
        print(f"could not write the job summary: {exc}", file=sys.stderr)


def call(query: str, variables: dict[str, object], key: str) -> dict | None:
    """Run one GraphQL call, or return ``None`` having said why it failed."""
    payload = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        LINEAR_API,
        data=payload,
        headers={"Authorization": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        note(f"Linear ping: the API returned HTTP {exc.code}; ping not delivered.")
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        note(f"Linear ping: could not reach the API ({exc}); ping not delivered.")
        return None
    except json.JSONDecodeError:
        note("Linear ping: the API returned something that is not JSON.")
        return None

    if body.get("errors"):
        note(f"Linear ping: the API reported {body['errors']}; ping not delivered.")
        return None
    return body.get("data")


def _attached_issues(data: dict) -> list[dict]:
    """Pull the issues out of the attachment query's answer, tolerating any shape.

    `data.attachmentsForURL` can be null, a node's `issue` can be null, and a
    hypothetical issue could arrive without an `identifier`. None of those is worth
    failing a review round over, so each reads as "nothing found" (Cursor, C2 on #379).
    """
    if not isinstance(data, dict):
        return []
    container = data.get("attachmentsForURL")
    nodes = (container or {}).get("nodes") or []
    issues = []
    for node in nodes:
        issue = (node or {}).get("issue")
        if isinstance(issue, dict) and issue.get("id") and issue.get("identifier"):
            issues.append(issue)
    return issues


def issue_by_attachment(pr_url: str, key: str) -> tuple[str, str] | None:
    """Return the ``(id, identifier)`` of the issue this pull request is attached to."""
    data = call(ATTACHMENT_QUERY, {"url": pr_url}, key)
    if data is None:
        return None
    issues = _attached_issues(data)
    if not issues:
        print("Linear ping: no issue holds this pull request.", file=sys.stderr)
        return None
    if len(issues) > 1:
        # Not fatal, but two issues claim this pull request, and whichever one is
        # commented on, the other is the one someone is waiting on.
        note(
            "Linear ping: more than one issue is attached to this pull request; "
            "using the first. Which ones they are is on Linear, not in this log."
        )
    return issues[0]["id"], issues[0]["identifier"]


def issue_by_body(pr_body: str, key: str) -> tuple[str, str] | None:
    """Return the issue named by a legacy ``Linear Issue:`` footer, if there is one."""
    found = find_issue(pr_body)
    if found is None:
        return None
    team, number = found
    data = call(ISSUE_QUERY, {"team": team, "number": float(number)}, key)
    if data is None:
        return None
    nodes = (data.get("issues") or {}).get("nodes") or []
    if not nodes:
        note(
            "Linear ping: the pull request body names an issue that is not visible to "
            "this API key."
        )
        return None
    first = nodes[0]
    if not (first.get("id") and first.get("identifier")):
        return None
    return first["id"], first["identifier"]


def _run(args: argparse.Namespace) -> None:
    """Resolve the issue and post the comment, saying which case it was either way."""
    comment = open(args.body_file, encoding="utf-8").read()

    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        # No key means no lookup of any kind, so a person doing this by hand has to
        # find the issue themselves — it is the one the pull request is attached to.
        # The old wording named the issue out of the body footer, which published a
        # tracker key into a public Actions log (Cursor, C6 on #379).
        note(
            "Linear ping: LINEAR_API_KEY is not set, so no Linear issue was commented "
            "on and the findings ping was posted only on the pull request. To do it by "
            "hand, open the issue this pull request is attached to."
        )
        return

    issue = issue_by_attachment(args.pr_url, key)
    if issue is None and args.pr_body_file:
        issue = issue_by_body(open(args.pr_body_file, encoding="utf-8").read(), key)
    if issue is None:
        note(
            f"Linear ping: no Linear issue holds {args.pr_url} as an attachment, and "
            "its body names none either, so the findings ping was posted only on the "
            "pull request. If the implementer is a Cursor agent whose session has "
            "ended, it will not see it. Attaching the pull request to its issue is "
            "what this looks for."
        )
        return
    issue_id, _identifier = issue

    if args.dry_run:
        # Through `note`, not `print`: the check workflow tells a person to read the
        # job summary, and a line that only reaches stdout is not there (C3 on #379).
        note(
            "Linear ping: resolved the issue this pull request is attached to. "
            "Not posted, this being a dry run. The comment would have been:\n\n"
            f"{comment}"
        )
        return

    result = call(COMMENT_MUTATION, {"issueId": issue_id, "body": comment}, key)
    if result is None:
        return
    if not (result.get("commentCreate") or {}).get("success"):
        note("Linear ping: Linear declined the comment.")
        return

    note("Linear ping: delivered to the issue this pull request is attached to.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pr-url",
        required=True,
        help="the pull request's URL, which Linear holds as an attachment on its issue",
    )
    parser.add_argument(
        "--pr-body-file",
        help="file holding the pull request body, read only if the attachment lookup "
        "finds nothing (bodies opened before 2026-09-20 carry a tracker footer)",
    )
    parser.add_argument(
        "--body-file", required=True, help="file holding the comment to post"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve the issue against the live API and report what would be posted, "
        "without posting it",
    )
    args = parser.parse_args()

    try:
        _run(args)
    except Exception as exc:  # noqa: BLE001 - deliberately total; see the module header
        # The module header promises this hop cannot fail a review round, and until now
        # that held only for the failures `call` already mapped to None — a null field,
        # a missing key in a response, an undecodable body would each have exited
        # non-zero, killing the step before `loop-status.sh` ran and leaving the labels
        # flipped with no status comment (Cursor, C2 on #379). The promise is the point,
        # so it is enforced here rather than enumerated.
        note(f"Linear ping: unexpected {type(exc).__name__}; ping not delivered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
