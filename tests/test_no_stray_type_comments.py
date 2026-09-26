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
docstring in a scanned file is not a comment and does not count.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

# A comment whose first token is "type:", where what follows is not the `ignore`
# directive. The whitespace sits inside the lookahead on purpose: with it
# outside, `\s*` backtracks to empty and the lookahead is tested against
# " ignore", which does not start with "ignore", so real directives get flagged.
_STRAY_TYPE_COMMENT = re.compile(r"#\s*type:(?!\s*ignore\b)")


def _stray_type_comments(path: Path, root: Path = SRC.parent) -> list[str]:
    """Offending comments in ``path``, reported relative to ``root``.

    ``root`` is only how the offender is named in the failure message; the
    tests below scan a file outside the repository and pass their own.
    """
    source = path.read_text(encoding="utf-8")
    found: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        if _STRAY_TYPE_COMMENT.match(token.string):
            line = token.start[0]
            found.append(f"{path.relative_to(root)}:{line}: {token.string}")
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


# What mypy actually does with each shape, measured on mypy 1.19.1 / CPython 3.11
# rather than assumed. The first three are why the guard exists; the rest are what
# it must leave alone, and every one of them is a mutation the guard could lose:
# widening the directive exemption puts `ignore` in the first group, and scanning
# lines instead of comment tokens takes the two trailing cases out of it.
_FLAGGED = [
    # `expected an indented block after 'except' statement` — and mypy then stops
    # checking everything downstream, which is the bug this whole change is about.
    (
        "own line, inside a block",
        "try:\n    pass\nexcept OSError:\n    # type: prose\n    raise\n",
    ),
    # `invalid syntax`.
    ("trailing a call", "f()  # type: prose\n"),
    # Parses, and mypy reads the prose as the type: `Syntax error in type comment`.
    ("trailing an assignment", "x = 1  # type: prose\n"),
]

_ALLOWED = [
    ("the ignore directive", "x = 1  # type: ignore\n"),
    ("a coded ignore directive", "x = 1  # type: ignore[assignment]\n"),
    ("an unspaced ignore directive", "x = 1  #type:ignore\n"),
    # mypy reads a type comment only after a single `#`, so `##` is prose to it too.
    ("prose behind a second hash", "x = 1  ## type: prose\n"),
    # Not a comment at all; the scan walks tokens, so it never sees this as one.
    ("the marker inside a string", "s = '# type: prose'\n"),
]


@pytest.mark.parametrize(("name", "source"), _FLAGGED, ids=[c[0] for c in _FLAGGED])
def test_the_guard_flags_prose_mypy_would_read_as_a_type(
    name: str, source: str, tmp_path: Path
) -> None:
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")
    assert _stray_type_comments(path, root=tmp_path), f"{name} should have been flagged"


@pytest.mark.parametrize(("name", "source"), _ALLOWED, ids=[c[0] for c in _ALLOWED])
def test_the_guard_leaves_alone_what_mypy_accepts(
    name: str, source: str, tmp_path: Path
) -> None:
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")
    assert not _stray_type_comments(path, root=tmp_path), (
        f"{name} should not have been flagged"
    )
