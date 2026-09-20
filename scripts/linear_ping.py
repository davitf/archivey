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

**Nothing here is allowed to break the loop.** A pull request with no Linear link, a
missing API key, or a Linear outage all end in a warning and exit 0: the findings are
already posted on the pull request, and failing the run would lose that for a hop that
is a convenience. The one thing this must not do is fail *silently*, so every skip and
every error says which one it was, on stderr and in the job summary.

Usage, from the loop workflow:

    scripts/linear_ping.py --pr-body-file pr.md --body-file ping.md

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pr-body-file",
        required=True,
        help="file holding the pull request body, which names the Linear issue",
    )
    parser.add_argument(
        "--body-file", required=True, help="file holding the comment to post"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve the issue and print what would be posted, without posting",
    )
    args = parser.parse_args()

    pr_body = open(args.pr_body_file, encoding="utf-8").read()
    comment = open(args.body_file, encoding="utf-8").read()

    found = find_issue(pr_body)
    if found is None:
        note(
            "Linear ping: this pull request body names no Linear issue, so the "
            "findings ping was posted only on the pull request. If the implementer "
            "is a Cursor agent whose session has ended, it will not see it."
        )
        return 0
    team, number = found
    identifier = f"{team}-{number}"

    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        note(
            f"Linear ping: LINEAR_API_KEY is not set, so {identifier} was not "
            "commented on and the findings ping was posted only on the pull request. "
            "Set the secret to close the loop's one known delivery gap."
        )
        return 0

    if args.dry_run:
        print(f"would comment on {identifier}:\n\n{comment}")
        return 0

    data = call(ISSUE_QUERY, {"team": team, "number": float(number)}, key)
    if data is None:
        return 0
    nodes = data.get("issues", {}).get("nodes") or []
    if not nodes:
        note(f"Linear ping: no issue {identifier} is visible to this API key.")
        return 0

    result = call(COMMENT_MUTATION, {"issueId": nodes[0]["id"], "body": comment}, key)
    if result is None:
        return 0
    if not result.get("commentCreate", {}).get("success"):
        note(f"Linear ping: Linear declined the comment on {identifier}.")
        return 0

    print(f"Linear ping: delivered to {identifier}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
