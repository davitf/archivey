"""The child-process worker scripts, as files: what they must share without importing.

Each worker runs as a script (``python -P <worker>.py``) and imports nothing from
``archivey`` or from the other worker, so code both need is copied. This module checks
that the copies stay the same. It needs neither rapidgzip nor pyppmd: the workers
import only the standard library at module level.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from archivey.internal.streams import ppmd_worker, rapidgzip_worker


def _code_without_docstring(function: object) -> str:
    """``function``'s code as an AST dump, without its docstring, comments or layout."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))  # type: ignore[arg-type]
    body = tree.body[0]
    assert isinstance(body, ast.FunctionDef)
    if (
        body.body
        and isinstance(body.body[0], ast.Expr)
        and isinstance(body.body[0].value, ast.Constant)
        and isinstance(body.body[0].value.value, str)
    ):
        body.body = body.body[1:]
    return ast.dump(body)


def test_both_workers_turn_off_core_dumps_the_same_way() -> None:
    """``disable_core_dumps`` is copied into both workers; only the docstrings, which
    say why each worker crashes, may differ. A change to one copy fails here until the
    other matches."""
    assert _code_without_docstring(
        rapidgzip_worker.disable_core_dumps
    ) == _code_without_docstring(ppmd_worker.disable_core_dumps)
