"""No comment in ``src/`` may begin with the ``# type:`` marker.

mypy reads a comment whose first token is ``type:`` as a type comment. When the
text after it is prose, mypy does not ignore it:

* in most positions the file fails to parse outright, with a misleading syntax
  error, and mypy then stops checking everything downstream of it;
* trailing a plain assignment it does parse, and mypy is handed the prose to
  read as a type instead.

archivey ships ``py.typed``, so either lands in the files of anyone
type-checking their own code against the library, and CI runs pyrefly and ty
rather than mypy, so nothing else here catches it.

``# type: ignore`` and ``# type: ignore[code]`` are the real directive and are
allowed. The scan reads comment *tokens* rather than lines, so it covers
trailing comments as well as own-line ones, and text inside a string or a
docstring — including this one — is not a comment and does not count.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

# A comment whose first token is "type:", where what follows is not the `ignore`
# directive. The whitespace sits inside the lookahead on purpose: with it
# outside, `\s*` backtracks to empty and the lookahead is tested against
# " ignore", which does not start with "ignore", so real directives get flagged.
_STRAY_TYPE_COMMENT = re.compile(r"#\s*type:(?!\s*ignore\b)")


def _stray_type_comments(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    found: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        if _STRAY_TYPE_COMMENT.match(token.string):
            line = token.start[0]
            found.append(f"{path.relative_to(SRC.parent)}:{line}: {token.string}")
    return found


def test_no_comment_starts_with_the_type_marker() -> None:
    assert SRC.is_dir(), f"{SRC} is not a directory; the scan would check nothing"
    files = sorted([*SRC.rglob("*.py"), *SRC.rglob("*.pyi")])
    assert files, f"no Python sources found under {SRC}"

    offenders = [line for path in files for line in _stray_type_comments(path)]
    assert not offenders, (
        "a comment beginning with the type-comment marker is read as a type "
        "comment by mypy; reword or rewrap so 'type:' is not the first token "
        "(`# type: ignore` is the real directive and is allowed):\n"
        + "\n".join(offenders)
    )
