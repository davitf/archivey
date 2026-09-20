"""Deliver the review loop's findings ping to Cursor, through Linear.

`dev-docs/review-loop.md` listed this as an unobserved rough edge: the workflow posts
the `@cursor` ping with `GITHUB_TOKEN`, so it arrives from `github-actions[bot]`, and
nobody had checked whether a Cursor background agent whose session has already ended
wakes up for a bot's GitHub comment. It does not. Measured on #374 (2026-09-20):

    00:14  Cursor's agent reports finished; its session ends.
    00:21  the workflow posts the `@cursor` ping on the pull request.
    ------ 66 minutes. No push, no comment, no new agent.
    01:27  the same ask, posted as a `@cursor` comment on the Linear issue.
    01:41  Cursor pushes all seven findings fixed, and asks for the next round.

Fourteen minutes against sixty-six of silence, and slowness is not the explanation —
the same agent went from Linear delegation to a finished pull request in about seven
minutes earlier that night. The delegation path is Linear-native, so that is where the
wake-up has to land.

This break is worse than it sounds, because the thirty-minute quiet period cannot
rescue it: the branch is quiet precisely because the implementer never learned there
was anything to do, so the pull request sits at `loop:round-1` until a person notices.

The GitHub comment is still posted. It is the record a human reads on the pull request,
and on a `claude/*` branch it is also the delivery mechanism — a Claude Code session
subscribed to the pull request's activity does see it. This script is the extra hop for
`cursor/*` branches only.

**Finding the issue.** Linear records the pull request as an *attachment* on the issue
it was delegated from, so the issue is looked up by the pull request's own URL. The
pull request body is only a fallback: Cursor used to append a `Linear Issue: [KEY](url)`
footer to every body it opened, which published a private tracker link on a public
repository — `AGENTS.md` forbids exactly that, and the maintainer ruled on 2026-09-20
that the footer goes. Bodies opened before that still carry it, and the fallback reads
them; nothing here depends on the footer coming back.

**Nothing here is allowed to break the loop.** A pull request no issue can be found for,
a missing API key, or a Linear outage all end in a warning and exit 0: the findings are
already posted on the pull request, and failing the run would lose that for a hop that
is a convenience. The one thing this must not do is fail *silently*, so every skip and
every error says which one it was, on stderr and in the job summary.

Usage, from the loop workflow:

    scripts/linear_ping.py --pr-url https://github.com/o/r/pull/1 --body-file ping.md

`--dry-run` resolves the issue against the live API and prints what it would post
without posting it, which is how to check the credential without spending a round.
`LINEAR_API_KEY` comes from the environment.
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

#: Cursor writes this line into every pull request body it opens, which is what makes
#: the issue recoverable without the loop carrying a second piece of per-PR state:
#:
#:     Linear Issue: [TEAM-123](https://linear.app/archivey/issue/TEAM-123/…)
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

#: The primary lookup. Linear attaches the pull request to the issue it delegated, so
#: the issue is reachable from the URL alone and the public body needs no tracker line.
#: ``first: 2`` rather than 1 on purpose: two issues claiming one pull request is a
#: state worth naming in the log rather than silently picking the first of.
ISSUE_BY_ATTACHMENT_QUERY = """
query IssueByAttachment($url: String!) {
  issues(filter: { attachments: { url: { eq: $url } } }, first: 2) {
    nodes { id identifier }
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


def issue_by_attachment(pr_url: str, key: str) -> tuple[str, str] | None:
    """Return the ``(id, identifier)`` of the issue this pull request is attached to."""
    data = call(ISSUE_BY_ATTACHMENT_QUERY, {"url": pr_url}, key)
    if data is None:
        return None
    nodes = data.get("issues", {}).get("nodes") or []
    if not nodes:
        return None
    if len(nodes) > 1:
        # Not fatal, but it means two issues claim this pull request, and whichever one
        # is commented on, the other is the one someone is waiting on.
        note(
            "Linear ping: more than one issue is attached to this pull request "
            f"({', '.join(n['identifier'] for n in nodes)}); using the first."
        )
    return nodes[0]["id"], nodes[0]["identifier"]


def issue_by_body(pr_body: str, key: str) -> tuple[str, str] | None:
    """Return the issue named by a legacy ``Linear Issue:`` footer, if there is one."""
    found = find_issue(pr_body)
    if found is None:
        return None
    team, number = found
    data = call(ISSUE_QUERY, {"team": team, "number": float(number)}, key)
    if data is None:
        return None
    nodes = data.get("issues", {}).get("nodes") or []
    if not nodes:
        note(f"Linear ping: no issue {team}-{number} is visible to this API key.")
        return None
    return nodes[0]["id"], nodes[0]["identifier"]


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
        help="resolve the issue against the live API and print what would be posted, "
        "without posting it",
    )
    args = parser.parse_args()

    comment = open(args.body_file, encoding="utf-8").read()

    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        # Name the issue if the body happens to carry one: a person reading the run can
        # then post the comment by hand, which is the documented workaround. Without the
        # key the attachment lookup cannot run, so this is the only name available.
        named = ""
        if args.pr_body_file:
            found = find_issue(open(args.pr_body_file, encoding="utf-8").read())
            if found is not None:
                named = f" The pull request body names {found[0]}-{found[1]}."
        note(
            "Linear ping: LINEAR_API_KEY is not set, so no Linear issue was commented "
            "on and the findings ping was posted only on the pull request." + named
        )
        return 0

    issue = issue_by_attachment(args.pr_url, key)
    if issue is None and args.pr_body_file:
        issue = issue_by_body(open(args.pr_body_file, encoding="utf-8").read(), key)
    if issue is None:
        note(
            f"Linear ping: no Linear issue is attached to {args.pr_url}, and its body "
            "names none either, so the findings ping was posted only on the pull "
            "request. If the implementer is a Cursor agent whose session has ended, it "
            "will not see it."
        )
        return 0
    issue_id, identifier = issue

    if args.dry_run:
        print(f"Linear ping: would comment on {identifier}:\n\n{comment}")
        return 0

    result = call(COMMENT_MUTATION, {"issueId": issue_id, "body": comment}, key)
    if result is None:
        return 0
    if not result.get("commentCreate", {}).get("success"):
        note(f"Linear ping: Linear declined the comment on {identifier}.")
        return 0

    print(f"Linear ping: delivered to {identifier}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
