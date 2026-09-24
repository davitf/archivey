"""The CLI is built on archivey's public API only.

``archivey.cli`` is the first consumer of the library and the example other front ends
copy. If it reaches into ``archivey.internal``, it can depend on something no caller can
rely on, and an internal refactor can break it without touching any public name. The
pieces a front end needs beyond the core surface live in :mod:`archivey.cli_helpers`.
See CONTRIBUTING.md, "The CLI uses only public API."
"""

from __future__ import annotations

import ast
from pathlib import Path

CLI_DIR = Path(__file__).resolve().parents[1] / "src" / "archivey" / "cli"


def _internal_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # relative: stays inside archivey.cli
            module = node.module or ""
            names = [module] + [f"{module}.{alias.name}" for alias in node.names]
        else:
            continue
        if any(
            name == "archivey.internal" or name.startswith("archivey.internal.")
            for name in names
        ):
            found.append(f"{path.name}:{node.lineno} {ast.unparse(node)}")
    return found


def test_cli_imports_nothing_from_internal() -> None:
    files = sorted(CLI_DIR.rglob("*.py"))
    assert files, f"no CLI sources under {CLI_DIR}"
    offending = [hit for path in files for hit in _internal_imports(path)]
    assert offending == [], (
        "archivey.cli must use only public API; move what it needs to a public module "
        f"(archivey.cli_helpers for front-end helpers): {offending}"
    )


def test_the_guard_sees_an_internal_import(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import archivey.internal.source\n"
        "from archivey import internal\n"
        "from archivey.internal.registry import get_registry\n",
        encoding="utf-8",
    )
    assert len(_internal_imports(probe)) == 3
