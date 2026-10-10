"""Guard: the top level of ``internal/streams/`` holds no codec-specific module.

Each codec's code (its ``StreamCodec``, its engine, its child-process worker) lives in
``internal/streams/codecs/``; generic stream tools live in ``internal/streams/streamtools/``.
The top level keeps only the layers every codec shares.

The allowed entries are the ``:mod:`.name``` items of the package map in
``internal/streams/__init__.py``, so the map is the single list: a new shared layer goes
there, and a codec's module goes in ``codecs/``. A top-level module must also not import
``codecs/``, since a layer every codec shares cannot depend on one codec.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path

import pytest

_STREAMS = (
    Path(__file__).resolve().parent.parent / "src" / "archivey" / "internal" / "streams"
)
_MAP_ENTRY = re.compile(r"^- :mod:`\.(\w+)`", re.MULTILINE)


def _package_map(streams: Path) -> set[str]:
    """The entry names listed in ``streams/__init__.py``'s package map."""
    docstring = ast.get_docstring(ast.parse((streams / "__init__.py").read_text()))
    return set(_MAP_ENTRY.findall(docstring or ""))


def _top_level(streams: Path) -> set[str]:
    """Every module and directory directly in ``streams``, by importable name."""
    names = set()
    for path in streams.iterdir():
        # __pycache__ appears once the suite has run; dot entries are tool state.
        if path.name == "__pycache__" or path.name.startswith("."):
            continue
        if path.is_dir():
            # A directory without __init__.py still imports, as a namespace package.
            names.add(path.name)
        elif path.suffix == ".py" and path.stem != "__init__":
            names.add(path.stem)
    return names


def _layout_problems(streams: Path) -> list[str]:
    listed, present = _package_map(streams), _top_level(streams)
    problems = [
        f"{name}: not in the package map of internal/streams/__init__.py; move a "
        "codec-specific module into codecs/, or list a shared layer in the map"
        for name in sorted(present - listed)
    ]
    problems += [
        f"{name}: listed in the package map of internal/streams/__init__.py but "
        "missing; remove the stale entry"
        for name in sorted(listed - present)
    ]
    return problems


def _codecs_imports(streams: Path) -> list[str]:
    """Top-level modules that import ``archivey.internal.streams.codecs``."""
    found = []
    for path in sorted(streams.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level and (module == "codecs" or module.startswith("codecs.")):
                    found.append(path.name)
                elif node.level == 1 and not module:
                    if any(alias.name == "codecs" for alias in node.names):
                        found.append(path.name)
                elif module.startswith("archivey.internal.streams.codecs"):
                    found.append(path.name)
            elif isinstance(node, ast.Import):
                if any(
                    alias.name.startswith("archivey.internal.streams.codecs")
                    for alias in node.names
                ):
                    found.append(path.name)
    return found


def test_streams_top_level_matches_the_package_map() -> None:
    assert _layout_problems(_STREAMS) == []


def test_streams_top_level_does_not_import_codecs() -> None:
    assert _codecs_imports(_STREAMS) == [], (
        "a shared streams layer must not import codecs/; move the module into codecs/"
    )


def _fake_streams(tmp_path: Path) -> Path:
    streams = tmp_path / "streams"
    (streams / "codecs").mkdir(parents=True)
    (streams / "__pycache__").mkdir()
    (streams / "__init__.py").write_text(
        '"""Package map:\n\n- :mod:`.codecs` - codecs.\n- :mod:`.verify` - verify.\n"""\n'
    )
    (streams / "verify.py").write_text("")
    return streams


def test_the_guard_accepts_a_matching_layout(tmp_path: Path) -> None:
    streams = _fake_streams(tmp_path)
    assert _layout_problems(streams) == []
    assert _codecs_imports(streams) == []


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda s: (s / "gzip_engine.py").write_text(""), id="new-module"),
        pytest.param(
            lambda s: (
                (s / "engines").mkdir() or (s / "engines" / "gz.py").write_text("")
            ),
            id="namespace-dir",
        ),
        pytest.param(lambda s: (s / "verify.py").unlink(), id="stale-entry"),
    ],
)
def test_the_guard_reports_a_layout_change(
    tmp_path: Path, mutate: Callable[[Path], object]
) -> None:
    streams = _fake_streams(tmp_path)
    mutate(streams)
    assert _layout_problems(streams) != []


@pytest.mark.parametrize(
    "source",
    [
        "from .codecs import registry\n",
        "from . import codecs\n",
        "from .codecs.base import Codec\n",
        "import archivey.internal.streams.codecs.base\n",
        "from archivey.internal.streams.codecs import base\n",
    ],
)
def test_the_guard_reports_a_codecs_import(tmp_path: Path, source: str) -> None:
    streams = _fake_streams(tmp_path)
    (streams / "verify.py").write_text(source)
    assert _codecs_imports(streams) == ["verify.py"]
