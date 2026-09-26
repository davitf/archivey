#!/usr/bin/env python3
"""Decide what the review hub watchdog should do, given the hub's current facts.

Stdlib only, no GitHub access: the workflow collects the facts, this script makes the
decision. That split is the point, and it is borrowed from `review_loop_gate.py` for the
same reason — except that here it is sharper, because **everything worth testing is dead
in normal operation**. The watchdog reads `OPEN` and exits on every push to `main`; the
branch that names which surface closed the hub runs only on the one event the workflow
exists for. A break in it is invisible until then, and the first version of this logic
was broken exactly that way (`gh api --paginate` runs `--jq` once per page, so a filter
ending in `last` returned one answer per page and the branch test could never take its
other arm).

Reads one JSON object on stdin, writes one JSON object on stdout:

    {"state": "OPEN" | "CLOSED" | "MERGED",
     "labels": ["loop:off", ...],
     "timeline": [{"event": "closed", "commit_id": "0454c54…"}, ...]}

    -> {"action": "none" | "reopen" | "fail",
        "reason": "…",              # one line, for the job summary
        "surface": "body" | "commit" | "unknown",
        "commit": "0454c54…" | null}

`timeline` is the issues-API timeline in the order GitHub serves it, oldest first, with
no filtering or reduction applied by the caller. Reducing it is this script's job
precisely because that is where the bug was.
"""

from __future__ import annotations

import json
import sys

#: Closing the hub deliberately is legitimate; this label says so and the watchdog then
#: leaves it alone. It exists on the repository — a label the job only ever *reads* is
#: one nobody notices is missing, and this one was missing when the watchdog first
#: shipped, so the escape hatch failed on use.
DELIBERATE_LABEL = "hub:closed-on-purpose"


def decide(facts: dict) -> dict:
    """Return the watchdog's decision for one reading of the hub's state."""
    state = facts.get("state")

    if state == "OPEN":
        return _answer("none", "the hub is open; nothing to do")

    if state == "MERGED":
        return _answer(
            "fail",
            "the hub is MERGED, which cannot be reopened; its threads need rehousing by hand",
        )

    if DELIBERATE_LABEL in (facts.get("labels") or []):
        return _answer(
            "none", f"the hub is closed and labelled {DELIBERATE_LABEL}; leaving it"
        )

    # A `closed` event driven by a commit message carries that commit; one driven by a
    # merged pull request's body carries none. That is the only way to tell the two
    # apart after the fact, and telling them apart is the whole of what this watchdog
    # adds over an unconditional reopen.
    #
    # Two things here are load-bearing and both have been wrong in a shipped version:
    # only `closed` events count (`referenced` events carry a `commit_id` too, and the
    # hub has hundreds of them), and it is the *last* of them, not the first.
    closes = [e for e in facts.get("timeline") or [] if e.get("event") == "closed"]

    if not closes:
        return _answer(
            "reopen",
            "the hub is closed but its timeline carries no `closed` event",
            surface="unknown",
        )

    commit = closes[-1].get("commit_id") or None
    if commit is None:
        return _answer(
            "reopen",
            "the hub was closed by a merged pull request's body",
            surface="body",
        )
    return _answer(
        "reopen",
        f"the hub was closed by a commit message, {commit}",
        surface="commit",
        commit=commit,
    )


def _answer(
    action: str, reason: str, *, surface: str = "", commit: str | None = None
) -> dict:
    return {"action": action, "reason": reason, "surface": surface, "commit": commit}


def main() -> int:
    json.dump(decide(json.load(sys.stdin)), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
