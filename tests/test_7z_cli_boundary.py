"""Guard: a test runs the ``7z`` CLI through :func:`tests.conftest.run_7z`.

A ``7z`` run with no timeout once hung on a Windows CI leg. The per-test timeout then
killed the xdist worker and left no traceback. ``run_7z`` sets a timeout and gives the
child no stdin, so the hang becomes a ``subprocess.TimeoutExpired`` in the test that
caused it.

The scan reads every ``.py`` file under ``tests/`` and reports each process call
(``subprocess.run`` and the other runners, ``os.system``, ``os.popen``, ``os.exec*``,
``os.spawn*``) whose program is the literal ``"7z"``, or whose program it cannot read
as a string literal (a variable, a call, a starred list). Calls that start
``sys.executable`` or another literal program (``rar``, ``xz``, ``git``) are not
reported: the rule is about ``7z``, and reporting every such call would list 56
files, most of which never run ``7z``. Calls inside a function named ``run_7z`` are
the helper itself and are not reported. The scan also reports ``import subprocess as X``,
``import os as X`` and a ``from subprocess``/``from os`` import of a runner name,
because a call through those names hides from the scan.

Not every file is converted yet. ``_UNCONVERTED`` maps each remaining file to the
number of calls the scan reports in it, so a new reported call in any file fails, and
a converted file must be taken off the list. Some entries run a program the scan
cannot name, which may not be ``7z``; their count still stops a new ``7z`` call from
hiding among them.

Mutations it was checked against: adding ``subprocess.run(["7z", "l", str(driver)])``
to a test in ``tests/test_sevenzip_reader.py`` fails it, and so does adding a third
direct ``7z`` call to ``tests/test_zip.py``, which is on the list with two.
"""

from __future__ import annotations

import ast
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_HELPER = "run_7z"
_SUBPROCESS_RUNNERS = frozenset(
    {
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
    }
)

# Files that still run ``7z``, or a program the scan cannot name, directly, with the
# number of such calls in each. Lower or remove an entry when you convert calls.
_UNCONVERTED: dict[str, int] = {
    # Each reported call starts the literal "7z".
    "test_audit2_cross_format.py": 1,
    "test_audit2_zip.py": 1,
    "test_audit_cross_format.py": 2,
    "test_audit_sevenzip.py": 2,
    "test_decoder_limits.py": 4,
    "test_detection.py": 1,
    "test_error_translation.py": 1,
    "test_key_derivation_budget.py": 2,
    "test_link_target_cap.py": 1,
    "test_listing_limits.py": 1,
    "test_password.py": 4,
    "test_sevenzip_arm64.py": 4,
    "test_sevenzip_bcj2.py": 1,
    "test_sevenzip_parser_hardening.py": 1,
    "test_sevenzip_password_confirm.py": 1,
    "test_surplus_input.py": 2,
    "test_volumes.py": 1,
    "test_windows_reparse.py": 1,
    "test_zip.py": 2,
    "test_zip_aes.py": 2,
    "test_zip_native_codecs.py": 2,
    "test_zip_parser.py": 1,
    # At least one reported call starts a program the scan cannot name.
    "atheris_fuzz/__main__.py": 1,
    "fuzz_tar_parser.py": 2,
    "sample_archives.py": 2,
    "test_cli.py": 2,
    "test_codecs.py": 4,
    "test_extraction.py": 1,
    "test_iso_trailing_data.py": 1,
    "test_public_api.py": 1,
    "test_rar_unrar_names.py": 1,
    "test_review_loop_gate.py": 2,
    "test_sevenzip_surrogate_names.py": 1,
    "test_tar_parser_differential.py": 3,
    "test_utf16_surrogate_names.py": 1,
}


def _is_runner(module: str, name: str) -> bool:
    if module == "subprocess":
        return name in _SUBPROCESS_RUNNERS
    return module == "os" and (
        name in {"system", "popen"} or name.startswith(("exec", "spawn"))
    )


def _program(arg: ast.expr | None) -> str | None:
    """The program a process call starts, or ``None`` when it is not a literal."""
    while isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
        arg = arg.left
    if isinstance(arg, ast.List) and arg.elts:
        arg = arg.elts[0]
        if ast.unparse(arg) == "sys.executable":
            return "sys.executable"
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        words = arg.value.split()
        return words[0] if words else None
    return None


def _calls_that_bypass_run_7z(path: Path) -> list[str]:
    """Each place in ``path`` that runs ``7z``, or may run it, without the helper."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    in_helper = {
        id(node)
        for func in ast.walk(tree)
        if isinstance(func, ast.FunctionDef) and func.name == _HELPER
        for node in ast.walk(func)
    }
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(
                (node.lineno, f"import {alias.name} as {alias.asname}")
                for alias in node.names
                if alias.name in {"subprocess", "os"} and alias.asname
            )
        elif isinstance(node, ast.ImportFrom) and node.module in {"subprocess", "os"}:
            found.extend(
                (node.lineno, f"from {node.module} import {alias.name}")
                for alias in node.names
                if alias.name == "*" or _is_runner(node.module, alias.name)
            )
        elif (
            isinstance(node, ast.Call)
            and id(node) not in in_helper
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and _is_runner(node.func.value.id, node.func.attr)
            and _program(node.args[0] if node.args else None) in {"7z", None}
        ):
            found.append((node.lineno, f"{ast.unparse(node.func)}(...)"))
    return [f"{line}: {what}" for line, what in sorted(found)]


def test_7z_cli_runs_go_through_the_bounded_helper() -> None:
    found = {
        path.relative_to(_TESTS).as_posix(): calls
        for path in sorted(_TESTS.rglob("*.py"))
        if (calls := _calls_that_bypass_run_7z(path))
    }
    wrong = {
        name: calls
        for name, calls in found.items()
        if len(calls) != _UNCONVERTED.get(name)
    }
    gone = sorted(_UNCONVERTED.keys() - found.keys())
    assert not wrong and not gone, (
        "run the 7z CLI through tests.conftest.run_7z; when you convert calls, lower "
        f"or remove the file's count in _UNCONVERTED.\nCount differs: {wrong}\n"
        f"No longer reported: {gone}"
    )


def test_the_scan_finds_each_way_to_bypass_the_helper(tmp_path: Path) -> None:
    """The scan above must not pass because it cannot see a call."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import os, subprocess, sys\n"
        "import subprocess as sp\n"
        "from subprocess import TimeoutExpired, run\n"
        "subprocess.run([sys.executable, '-c', 'pass'])\n"
        "subprocess.run(['xz', '-d', 'x.xz'])\n"
        "subprocess.run(['rar', 'a'] + ['x.rar'])\n"
        "subprocess.run(['7z', 'a', 'x.7z'])\n"
        "subprocess.check_output(['7z', 'l'] + ['x.7z'])\n"
        "cmd = ['7z', 'l']\n"
        "subprocess.Popen(cmd)\n"
        "os.system('7z t x.7z')\n"
        "def run_7z(args):\n"
        "    return subprocess.run(['7z', *args])\n",
        encoding="utf-8",
    )
    assert _calls_that_bypass_run_7z(sample) == [
        "2: import subprocess as sp",
        "3: from subprocess import run",
        "7: subprocess.run(...)",
        "8: subprocess.check_output(...)",
        "10: subprocess.Popen(...)",
        "11: os.system(...)",
    ]
