"""No ``str`` regex in ``src`` uses ``\\d``, ``\\w`` or ``\\s`` without ``re.ASCII``.

On a ``str`` pattern ``\\d`` matches any Unicode digit, ``\\w`` any Unicode letter and
``\\s`` any Unicode space, so ``0.7z.\\u066000`` (Arabic-Indic zeros) read as a numbered
volume. Archive formats and tool banners use ASCII; write ``[0-9]``, ``[A-Za-z0-9_]``
or ``[ \\t]``, or pass ``re.ASCII``. ``bytes`` patterns are ASCII already.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "archivey"

_RE_FUNCS = {
    "compile",
    "search",
    "match",
    "fullmatch",
    "sub",
    "subn",
    "split",
    "findall",
    "finditer",
}
# ``\d`` / ``\w`` / ``\s`` not preceded by another backslash (``\\d`` is a literal ``\d``).
_UNICODE_CLASS = re.compile(r"(?<!\\)(?:\\\\)*\\[dswDSW]")


def _has_ascii_flag(node: ast.Call) -> bool:
    flags = [kw.value for kw in node.keywords if kw.arg == "flags"]
    flag_pos = {"compile": 1, "search": 2, "match": 2, "fullmatch": 2, "findall": 2}
    func = node.func
    assert isinstance(func, ast.Attribute)
    pos = flag_pos.get(func.attr)
    if pos is not None and len(node.args) > pos:
        flags.append(node.args[pos])
    for flag in flags:
        for sub in ast.walk(flag):
            if isinstance(sub, ast.Attribute) and sub.attr in ("ASCII", "A"):
                return True
    return False


def _offenders() -> list[str]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "re"
                and node.func.attr in _RE_FUNCS
                and node.args
            ):
                continue
            pattern = node.args[0]
            parts = (
                [pattern]
                if isinstance(pattern, ast.Constant)
                else [v for v in ast.walk(pattern) if isinstance(v, ast.Constant)]
            )
            text = "".join(
                p.value for p in parts if isinstance(p.value, str)
            )  # bytes patterns are skipped: they are ASCII-only already
            if not text or text.startswith("(?a"):
                continue
            if _UNICODE_CLASS.search(text) and not _has_ascii_flag(node):
                rel = path.relative_to(SRC.parent)
                found.append(f"{rel}:{node.lineno}: {text!r}")
    return found


def test_src_str_regexes_use_ascii_classes_only() -> None:
    offenders = _offenders()
    assert not offenders, (
        "str regexes using \\d, \\w or \\s without re.ASCII (write [0-9] / "
        "[A-Za-z0-9_] / [ \\t] instead):\n" + "\n".join(offenders)
    )
