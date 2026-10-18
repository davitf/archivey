"""``packages/archivey-cli`` stays in lockstep with the root package.

The companion distribution exists so ``uvx archivey-cli`` runs the CLI with
``[recommended]`` installed: Python packaging has no default extras, so ``uvx archivey``
gets the core only. It is published from the same tag as ``archivey``, so its version must
equal the root version, and its dependency must pin that same version. Otherwise a release
uploads an ``archivey-cli`` that installs an older or newer library than the tag.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def _project(path: Path) -> dict[str, Any]:
    with open(path, "rb") as fh:
        return tomllib.load(fh)["project"]


ROOT = _project(REPO_ROOT / "pyproject.toml")
COMPANION = _project(REPO_ROOT / "packages" / "archivey-cli" / "pyproject.toml")


def test_companion_version_matches_root() -> None:
    assert COMPANION["version"] == ROOT["version"]


def test_companion_pins_recommended_at_the_same_version() -> None:
    assert COMPANION["dependencies"] == [f"archivey[recommended]=={ROOT['version']}"]
    assert "recommended" in ROOT["optional-dependencies"]


def test_companion_scripts_run_the_root_cli() -> None:
    target = ROOT["scripts"]["archivey"]
    assert COMPANION["scripts"] == {"archivey": target, "archivey-cli": target}


def test_companion_python_floor_matches_root() -> None:
    assert COMPANION["requires-python"] == ROOT["requires-python"]
