"""The child-process worker scripts, as files: what they must share without importing.

Each worker runs as a script (``python -P <worker>.py``) and imports nothing from
``archivey`` or from the other worker, so code both need is copied. This module checks
that the copies stay the same. It needs neither rapidgzip nor pyppmd: the workers
import only the standard library at module level.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
import textwrap
from types import FunctionType

import pytest

from archivey.internal.streams import ppmd_worker, rapidgzip_worker


def _code_without_docstring(function: FunctionType) -> str:
    """``function``'s code as an AST dump, without its docstring, comments or layout."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
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


@pytest.mark.parametrize("worker", ["ppmd_worker", "rapidgzip_worker"])
def test_turning_off_core_dumps_survives_a_python_without_ctypes(worker: str) -> None:
    """A Python built without ``_ctypes`` (no libffi at build time, some minimal images)
    still runs the workers: turning dumps off is best effort, so a missing ``ctypes``
    skips the ``prctl`` step instead of ending the child before it decodes."""
    probe = textwrap.dedent(
        f"""
        import sys
        sys.modules["ctypes"] = None  # import ctypes now raises ImportError
        from archivey.internal.streams.{worker} import disable_core_dumps
        disable_core_dumps()
        print("returned")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "returned"
