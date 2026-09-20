"""No comment in ``src/`` may begin with the ``# type:`` marker.

mypy reads a comment whose first token is ``type:`` as a type comment. When the
text after it is prose, mypy does not just ignore it — it fails to parse the
whole file with a misleading syntax error and stops checking, which takes every
downstream file with it. archivey ships ``py.typed``, so that lands in the
files of anyone type-checking their own code against it, and CI runs pyrefly
and ty rather than mypy, so nothing else catches it.

``# type: ignore`` is the real directive and is allowed.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

# A comment whose first word is "type:", not followed by "ignore".
_STRAY_TYPE_COMMENT = re.compile(r"#\s*type:\s*(?!ignore\b)")


def test_no_prose_comment_starts_with_the_type_marker() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.lstrip()
            if not stripped.startswith("#"):
                continue
            if _STRAY_TYPE_COMMENT.match(stripped):
                offenders.append(f"{path.relative_to(SRC.parent)}:{lineno}: {stripped}")
    assert not offenders, (
        "a comment beginning with the type-comment marker breaks mypy's parse of "
        "the whole file; reword or rewrap so 'type:' is not the first word:\n"
        + "\n".join(offenders)
    )
