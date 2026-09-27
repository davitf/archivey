"""The CLI is built on archivey's public API only.

``archivey.cli`` is the first consumer of the library and the example other front ends
copy. If it reaches into ``archivey.internal``, it can depend on something no caller can
rely on, and an internal refactor can break it without touching any public name. The
display helpers a front end needs beyond the core surface live in :mod:`archivey.terminal`.
See CONTRIBUTING.md, "The CLI uses only public API."
"""

from __future__ import annotations

import ast
from pathlib import Path

CLI_DIR = Path(__file__).resolve().parents[1] / "src" / "archivey" / "cli"

# The one deliberate exception. ``--track-io`` reads the IO counters, which are not
# public API: the CLI is also a debugging tool for the library, so it may see what a
# caller cannot. Keyed by the hit text ``_internal_imports`` reports, minus the line
# number, so the entry names exactly one import statement and nothing else.
ALLOWED_INTERNAL_IMPORTS = frozenset(
    {
        "common.py: from archivey.internal.measurement import enable_measurement, io_stats",
    }
)


def _without_line(hit: str) -> str:
    location, _, statement = hit.partition(" ")
    return f"{location.rsplit(':', 1)[0]}: {statement}"


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
    hits = [hit for path in files for hit in _internal_imports(path)]
    offending = [
        hit for hit in hits if _without_line(hit) not in ALLOWED_INTERNAL_IMPORTS
    ]
    assert offending == [], (
        "archivey.cli must use only public API; move what it needs to a public module "
        f"(archivey.terminal for display helpers): {offending}"
    )
    # An allowlist entry whose import is gone would admit the next one silently.
    stale = ALLOWED_INTERNAL_IMPORTS - {_without_line(hit) for hit in hits}
    assert not stale, f"allowlisted CLI imports no longer present: {sorted(stale)}"


def test_the_guard_sees_an_internal_import(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import archivey.internal.source\n"
        "from archivey import internal\n"
        "from archivey.internal.registry import get_registry\n",
        encoding="utf-8",
    )
    assert len(_internal_imports(probe)) == 3
