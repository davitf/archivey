"""No ``re.compile`` pattern in ``src/`` may end in an unescaped ``$``.

Python's ``$`` also matches just before a final newline, so a name pattern
anchored with it accepts ``foo.part1.rar\\n`` as if it were ``foo.part1.rar``.
Patterns anchor the end of the string with ``\\Z`` instead (or are used with
``fullmatch``). A ``re.MULTILINE`` pattern is exempt: there ``$`` means end of
line, which is what it asks for.

The scan reads the AST, so it sees implicitly concatenated literals as the one
pattern Python compiles, and a ``$`` in a comment or docstring does not count.
A pattern built at runtime (a name, an f-string) is not checked.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"


def _literal_tail(node: ast.expr) -> str | bytes | None:
    """The end of a literal pattern; for ``a + b`` the end is ``b``'s."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_tail(node.right)
    return None


def _ends_in_unescaped_dollar(pattern: str | bytes) -> bool:
    text = pattern.decode("latin-1") if isinstance(pattern, bytes) else pattern
    if not text.endswith("$"):
        return False
    backslashes = len(text[:-1]) - len(text[:-1].rstrip("\\"))
    return backslashes % 2 == 0


def _is_re_compile(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "compile"
        and isinstance(func.value, ast.Name)
        and func.value.id == "re"
    )


def _is_multiline(node: ast.Call, pattern: str | bytes) -> bool:
    flags = [*node.args[1:], *(k.value for k in node.keywords if k.arg == "flags")]
    named = {
        sub.attr if isinstance(sub, ast.Attribute) else getattr(sub, "id", None)
        for flag in flags
        for sub in ast.walk(flag)
    }
    inline = pattern.startswith(b"(?m" if isinstance(pattern, bytes) else "(?m")
    return bool(named & {"MULTILINE", "M"}) or inline


def _dollar_anchored_patterns(path: Path, root: Path = SRC.parent) -> list[str]:
    """Offending ``re.compile`` calls in ``path``, reported relative to ``root``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_re_compile(node) and node.args):
            continue
        pattern = _literal_tail(node.args[0])
        if pattern is None or not _ends_in_unescaped_dollar(pattern):
            continue
        if _is_multiline(node, pattern):
            continue
        found.append(f"{path.relative_to(root)}:{node.lineno}: {ast.unparse(node)}")
    return found


def test_no_regex_in_src_ends_in_a_dollar_anchor() -> None:
    assert SRC.is_dir(), f"{SRC} is not a directory; the scan would check nothing"
    files = sorted(SRC.rglob("*.py"))
    assert files, f"no Python sources found under {SRC}"

    offenders = [line for path in files for line in _dollar_anchored_patterns(path)]
    assert not offenders, (
        "`$` also matches before a final newline; end the pattern with `\\Z` "
        "(or use fullmatch) unless it is a re.MULTILINE pattern:\n"
        + "\n".join(offenders)
    )


_FLAGGED = [
    ("plain", 're.compile(r"\\.rar$")\n'),
    ("with flags", 're.compile(r"\\.rar$", re.IGNORECASE)\n'),
    ("implicit concatenation", 're.compile(r"^(?P<base>.+)" r"\\.rar$")\n'),
    ("explicit concatenation", 're.compile(PREFIX + r"\\.rar$")\n'),
    ("bytes", 're.compile(rb"\\x00$")\n'),
    ("escaped backslash then dollar", 're.compile(r"\\\\$")\n'),
]

_ALLOWED = [
    ("end-of-string anchor", 're.compile(r"\\.rar\\Z")\n'),
    ("escaped dollar", 're.compile(r"price\\$")\n'),
    ("multiline flag", 're.compile(r"^x$", re.MULTILINE)\n'),
    ("multiline short flag", 're.compile(r"^x$", re.I | re.M)\n'),
    ("multiline keyword flag", 're.compile(r"^x$", flags=re.MULTILINE)\n'),
    ("inline multiline flag", 're.compile(r"(?m)^x$")\n'),
    ("not re.compile", 'fnmatch.translate("x$")\n'),
]


@pytest.mark.parametrize(("name", "source"), _FLAGGED, ids=[c[0] for c in _FLAGGED])
def test_the_guard_flags_a_dollar_anchor(
    name: str, source: str, tmp_path: Path
) -> None:
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")
    assert _dollar_anchored_patterns(path, root=tmp_path), f"{name} should be flagged"


@pytest.mark.parametrize(("name", "source"), _ALLOWED, ids=[c[0] for c in _ALLOWED])
def test_the_guard_leaves_other_patterns_alone(
    name: str, source: str, tmp_path: Path
) -> None:
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")
    assert not _dollar_anchored_patterns(path, root=tmp_path), (
        f"{name} should not have been flagged"
    )
