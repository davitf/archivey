"""No ``str`` regex in ``src`` matches Unicode where ASCII is meant.

On a ``str`` pattern without ``re.ASCII``:

- ``\\d`` matches any Unicode digit, ``\\w`` any Unicode letter, ``\\s`` any Unicode
  space, and ``\\b`` / ``\\B`` follow ``\\w``. So ``0.7z.\\u066000`` (Arabic-Indic zeros)
  read as a numbered volume.
- ``re.IGNORECASE`` folds ``\\u0131`` and ``\\u0130`` into ``i``, ``\\u017f`` into ``s`` and
  ``\\u212a`` into ``k``, so ``x.z\\u0131p.001`` would read as a ZIP volume.

Archive formats and tool banners use ASCII: write ``[0-9]``, ``[A-Za-z0-9_]`` or
``[ \\t]``, or pass ``re.ASCII``. ``bytes`` patterns are ASCII already and are skipped.

A pattern held in a module constant, or built from constants with ``+``, is read
through. Any other pattern this test cannot read is reported too, unless its call
site is listed in :data:`_GENERATED_PATTERN_SITES` with the reason it is safe.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "archivey"

# Each ``re`` function this test reads, with the index of its positional ``flags``.
_RE_FUNCS = {
    "compile": 1,
    "search": 2,
    "match": 2,
    "fullmatch": 2,
    "findall": 2,
    "finditer": 2,
    "split": 3,
    "sub": 4,
    "subn": 4,
}
# An escape not preceded by another backslash (``\\d`` is a literal ``\`` then ``d``).
_UNICODE_CLASS = re.compile(r"(?<!\\)(?:\\\\)*\\[dswbDSWB]")
_INLINE_FLAGS = re.compile(r"\(\?([aiLmsux]+)[:)]")

# Call sites whose pattern is built at run time, as ``(module, qualified name)``, and why
# each is safe. Add one only with that reason.
_GENERATED_PATTERN_SITES: dict[tuple[str, str], str] = {
    # ``_glob_to_regex`` emits only ``re.escape``d literals, ``[^/]``, ``[^/]*``,
    # ``.*`` and group syntax.
    ("archivey/paths.py", "_MemberGlob.__init__"): "built by _glob_to_regex",
    ("archivey/paths.py", "_compile_alternatives"): "joins _glob_to_regex output",
}


def _flag_names(node: ast.Call, flags_pos: int) -> set[str]:
    exprs = [kw.value for kw in node.keywords if kw.arg == "flags"]
    if len(node.args) > flags_pos:
        exprs.append(node.args[flags_pos])
    return {
        sub.attr
        for expr in exprs
        for sub in ast.walk(expr)
        if isinstance(sub, ast.Attribute)
    }


class _Module:
    def __init__(self, path: Path) -> None:
        self.tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        self.re_names = {"re"}
        self.func_names: dict[str, str] = {}
        self.constants: dict[str, ast.expr] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                self.re_names |= {
                    a.asname or a.name for a in node.names if a.name == "re"
                }
            elif isinstance(node, ast.ImportFrom) and node.module == "re":
                for a in node.names:
                    if a.name in _RE_FUNCS:
                        self.func_names[a.asname or a.name] = a.name
        for node in self.tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    self.constants[target.id] = node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                if isinstance(node.target, ast.Name):
                    self.constants[node.target.id] = node.value

    def re_func(self, node: ast.Call) -> str | None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in self.re_names
            and func.attr in _RE_FUNCS
        ):
            return func.attr
        if isinstance(func, ast.Name):
            return self.func_names.get(func.id)
        return None

    def pattern_text(self, expr: ast.expr, depth: int = 0) -> str | bytes | None:
        """The pattern ``expr`` spells, or ``None`` when it is not a constant."""
        if depth > 10:
            return None
        if isinstance(expr, ast.Constant) and isinstance(expr.value, (str, bytes)):
            return expr.value
        if isinstance(expr, ast.Name) and expr.id in self.constants:
            return self.pattern_text(self.constants[expr.id], depth + 1)
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            left = self.pattern_text(expr.left, depth + 1)
            right = self.pattern_text(expr.right, depth + 1)
            if isinstance(left, str) and isinstance(right, str):
                return left + right
            if isinstance(left, bytes) and isinstance(right, bytes):
                return left + right
        return None

    def holds_bytes(self, expr: ast.expr, depth: int = 0) -> bool:
        """Whether ``expr`` (through module constants) has a ``bytes`` literal in it,
        and so builds a ``bytes`` pattern."""
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, bytes):
                return True
            if (
                isinstance(sub, ast.Name)
                and sub.id in self.constants
                and depth < 10
                and self.holds_bytes(self.constants[sub.id], depth + 1)
            ):
                return True
        return False


def _calls_by_function(tree: ast.Module) -> list[tuple[str, ast.Call]]:
    found: list[tuple[str, ast.Call]] = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = child.name if scope == "<module>" else f"{scope}.{child.name}"
            if isinstance(child, ast.Call):
                found.append((scope, child))
            visit(child, inner)

    visit(tree, "<module>")
    return found


def _problems(text: str, flags: set[str]) -> list[str]:
    inline = "".join(_INLINE_FLAGS.findall(text))
    if flags & {"ASCII", "A"} or "a" in inline:
        return []
    problems = []
    if _UNICODE_CLASS.search(text):
        problems.append("\\d/\\w/\\s/\\b")
    if flags & {"IGNORECASE", "I"} or "i" in inline:
        problems.append("IGNORECASE")
    return problems


def _offenders(src: Path = SRC) -> list[str]:
    found = []
    for path in sorted(src.rglob("*.py")):
        module = _Module(path)
        rel = path.relative_to(src.parent).as_posix()
        for scope, node in _calls_by_function(module.tree):
            name = module.re_func(node)
            if name is None or not node.args:
                continue
            pattern = node.args[0]
            text = module.pattern_text(pattern)
            if isinstance(text, bytes) or module.holds_bytes(pattern):
                continue
            if text is None:
                if (rel, scope) not in _GENERATED_PATTERN_SITES:
                    found.append(f"{rel}:{node.lineno}: cannot read the pattern")
                continue
            problems = _problems(text, _flag_names(node, _RE_FUNCS[name]))
            if problems:
                found.append(f"{rel}:{node.lineno}: {', '.join(problems)}: {text!r}")
    return found


def test_src_str_regexes_use_ascii_classes_only() -> None:
    offenders = _offenders()
    assert not offenders, (
        "str regexes that match Unicode without re.ASCII (write [0-9] / "
        "[A-Za-z0-9_] / [ \\t], or add re.ASCII):\n" + "\n".join(offenders)
    )


_CAUGHT = {
    "class": 'import re\nX = re.compile(r"\\d")\n',
    "word boundary": 'import re\nX = re.compile(r"\\bRAR\\b")\n',
    "ignorecase": 'import re\nX = re.compile(r"\\.sfx", re.IGNORECASE)\n',
    "inline ignorecase": 'import re\nX = re.compile(r"(?i)\\.sfx")\n',
    "hoisted constant": 'import re\nP = r"\\d{3}"\nX = re.compile(P)\n',
    "concatenation": 'import re\nH = r"\\d"\nX = re.compile(H + r"[0-9]")\n',
    "alias": 'import re as _re\nX = _re.compile(r"\\d")\n',
    "from import": 'from re import search\nsearch(r"\\d", "a")\n',
    "unreadable": "import re\ndef f(p):\n    return re.compile(p)\n",
}
_PASSED = {
    "ascii class": 'import re\nX = re.compile(r"[0-9]")\n',
    "ascii flag": 'import re\nX = re.compile(r"\\d", re.IGNORECASE | re.ASCII)\n',
    "inline ascii": 'import re\nX = re.compile(r"(?ai)\\d")\n',
    "positional flag": 'import re\nre.sub(r"\\d", "y", "a", 0, re.ASCII)\n',
    "finditer flag": 'import re\nre.finditer(r"\\d", "a", re.ASCII)\n',
    "bytes": 'import re\nX = re.compile(rb"\\d", re.IGNORECASE)\n',
    "built bytes": 'import re\nB = re.escape(b"a") + rb"\\d"\nX = re.compile(B)\n',
    "escaped backslash": 'import re\nX = re.compile(r"\\\\d")\n',
}


def test_guard_catches_each_unicode_shape(tmp_path: Path) -> None:
    root = tmp_path / "archivey"
    root.mkdir()
    for name, source in {**_CAUGHT, **_PASSED}.items():
        (root / f"{name.replace(' ', '_')}.py").write_text(source, encoding="utf-8")
    reported = {line.split(":")[0] for line in _offenders(root)}
    assert reported == {f"archivey/{n.replace(' ', '_')}.py" for n in _CAUGHT}
