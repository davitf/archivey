"""Guard: the top level of ``internal/streams/`` holds no codec-specific module.

Each codec's code (its ``StreamCodec``, its engine, its child-process worker) lives in
``internal/streams/codecs/``; generic stream tools live in ``internal/streams/streamtools/``.
The top level keeps only the layers every codec shares. Engines once sat at the top
level beside their codecs' callers, and moving them took a PR of its own.

A new top-level entry fails this test on purpose: put a codec's module in ``codecs/``,
or add a shared layer to the list below when it really is codec-neutral.
"""

from __future__ import annotations

from pathlib import Path

_STREAMS = (
    Path(__file__).resolve().parent.parent / "src" / "archivey" / "internal" / "streams"
)

_ALLOWED_MODULES = frozenset(
    {
        "__init__.py",
        "archive_stream.py",
        "child_process.py",
        "counting.py",
        "crypto.py",
        "decompressor_stream.py",
        "resume.py",
        "verify.py",
    }
)
_ALLOWED_PACKAGES = frozenset({"codecs", "streamtools"})


def _top_level() -> tuple[set[str], set[str]]:
    """The ``.py`` modules and the packages directly in ``internal/streams/``."""
    modules = {path.name for path in _STREAMS.glob("*.py")}
    packages = {
        path.name
        for path in _STREAMS.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }
    return modules, packages


def test_streams_top_level_holds_only_shared_layers() -> None:
    modules, packages = _top_level()
    assert modules - _ALLOWED_MODULES == set(), (
        "put codec-specific modules in internal/streams/codecs/, not at the top level "
        "of internal/streams/"
    )
    assert packages - _ALLOWED_PACKAGES == set(), (
        "a new package under internal/streams/ needs a place in the layering"
    )


def test_the_allowed_entries_all_exist() -> None:
    """A stale entry in the lists above would let a new module of that name in."""
    modules, packages = _top_level()
    assert _ALLOWED_MODULES - modules == set()
    assert _ALLOWED_PACKAGES - packages == set()
