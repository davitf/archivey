"""Assert no finished OpenSpec change is left unarchived on `main`.

Merging a change does not apply it. The deltas under `openspec/changes/<change>/specs/`
only reach the authoritative `openspec/specs/` when someone runs `openspec archive`, and
that step is easy to forget because it happens *after* the PR merges rather than in it.

Three changes in a row (#212, #213, #214) merged complete and unarchived, each time
leaving `openspec/specs/` describing an API or a set of extras that no longer shipped.
The specs are the authority, so the window between merge and archive is a window where
the authority is wrong.

A change is "finished" here on the same terms `openspec list` uses to print
`✓ Complete`: its `tasks.md` has at least one checkbox and every one of them is `[x]`.
Anything else — `[ ]`, or the `[~]` partial marker used in a couple of archived changes —
counts as outstanding, so this errs toward staying quiet.

Runs on pushes to `main` only, not on pull requests: the implementing PR is *expected* to
check off its last task while still unarchived, so a PR-scoped check would fail exactly
the workflow it is meant to protect. Also runnable by hand:

    uv run python scripts/check_openspec_archived.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGES = Path(__file__).resolve().parent.parent / "openspec" / "changes"

# `- [x] 1.1 …`, at any indent. The archived corpus also uses `- [~]` for partially-done
# work, which this deliberately does not treat as done.
CHECKBOX = re.compile(r"^\s*- \[(.)\]", re.MULTILINE)

finished: list[str] = []
for change in sorted(CHANGES.iterdir()):
    # `archive/` holds the already-archived changes; everything else is in flight.
    if not change.is_dir() or change.name == "archive":
        continue

    tasks = change / "tasks.md"
    if not tasks.is_file():
        # A change can legitimately exist before its tasks are written; nothing to judge.
        continue

    marks = CHECKBOX.findall(tasks.read_text(encoding="utf-8"))
    if marks and all(mark in "xX" for mark in marks):
        finished.append(f"{change.name} ({len(marks)} tasks, all complete)")

if finished:
    listing = "\n".join(f"  - {name}" for name in finished)
    sys.exit(
        f"FAIL: {len(finished)} finished OpenSpec change(s) are still unarchived, so\n"
        f"their deltas have not been applied to openspec/specs/:\n\n"
        f"{listing}\n\n"
        f"Archive each one, then commit the resulting spec updates:\n\n"
        f"  npm install -g @fission-ai/openspec\n"
        f"  openspec archive <change> --yes\n\n"
        f"If a change is complete but should NOT be archived yet, leave its remaining\n"
        f"task unchecked rather than checking it off early."
    )

print("no finished OpenSpec change is left unarchived")
