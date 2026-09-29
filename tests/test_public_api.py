"""Guards on the public ``archivey`` namespace.

``archivey.__all__`` is curated by hand (not generated) so it lists only the public
API and not re-imported helpers like ``version``/``PackageNotFoundError``. This test
is the safety net that keeps the hand-maintained list from drifting.
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import sys
import typing
from pathlib import Path
from types import FunctionType

import pytest

import archivey


def test_archive_reader_is_an_abstract_public_interface() -> None:
    """The public ``ArchiveReader`` is an abstract interface, not the internal helper."""
    with pytest.raises(TypeError):
        archivey.ArchiveReader()  # type: ignore[abstract]  # abstract: cannot instantiate


def test_open_archive_returns_an_archive_reader(tmp_path) -> None:
    (tmp_path / "f.txt").write_bytes(b"x")
    with archivey.open_archive(tmp_path) as ar:
        assert isinstance(ar, archivey.ArchiveReader)


# The four methods a streaming reader refuses at run time, which the narrower
# ``StreamingArchiveReader`` type leaves out so a type checker refuses them too.
_RANDOM_ACCESS_METHODS = {"members", "get", "open", "read"}


def test_streaming_reader_type_leaves_out_exactly_the_random_access_methods() -> None:
    assert issubclass(archivey.ArchiveReader, archivey.StreamingArchiveReader)
    streaming_api = {
        name
        for name in dir(archivey.StreamingArchiveReader)
        if not name.startswith("_") or name in ("__iter__", "__contains__")
    }
    full_api = {
        name
        for name in dir(archivey.ArchiveReader)
        if not name.startswith("_") or name in ("__iter__", "__contains__")
    }
    assert full_api - streaming_api == _RANDOM_ACCESS_METHODS


def test_a_streaming_reader_is_still_an_archive_reader_at_run_time(tmp_path) -> None:
    """The narrowing is static only: one runtime class serves both access modes."""
    (tmp_path / "f.txt").write_bytes(b"x")
    with archivey.open_archive(tmp_path, streaming=True) as ar:
        assert isinstance(ar, archivey.ArchiveReader)
        with pytest.raises(archivey.ArchiveyUsageError):
            ar.members()  # type: ignore[attr-defined]  # the call this type refuses


_OPEN_ARCHIVE_TYPING_SAMPLE = """\
from typing import assert_type

import archivey
from archivey import ArchiveReader, StreamingArchiveReader


def check(flag: bool) -> None:
    assert_type(archivey.open_archive("a.zip"), ArchiveReader)
    assert_type(archivey.open_archive("a.zip", streaming=False), ArchiveReader)
    assert_type(archivey.open_archive("a.zip", streaming=True), StreamingArchiveReader)
    assert_type(archivey.open_archive("a.zip", streaming=flag), StreamingArchiveReader)
    with archivey.open_archive("a.zip") as full:
        assert_type(full, ArchiveReader)
        full.members()
    with archivey.open_archive("a.zip", streaming=True) as forward:
        assert_type(forward, StreamingArchiveReader)
        forward.members()
"""


def _checker_command(checker: str, exe: str, tmp_path: Path, src: Path) -> list[str]:
    """How to run ``checker`` on ``sample.py`` in ``tmp_path``, with ``src`` importable."""
    if checker == "ty":
        return [
            exe,
            "check",
            "--python",
            sys.executable,
            "--extra-search-path",
            str(src),
            "--output-format",
            "concise",
            "sample.py",
        ]
    # Pyrefly takes the search path from a config file; without one it falls back to a
    # preset that reports nothing here.
    (tmp_path / "pyrefly.toml").write_text(
        f'search-path = [{json.dumps(src.as_posix())}]\npython_version = "3.11"\n'
    )
    return [exe, "check", "--output-format", "min-text", "sample.py"]


@pytest.mark.parametrize("checker", ["ty", "pyrefly"])
def test_type_checker_refuses_random_access_on_a_streaming_reader(
    checker: str, tmp_path: Path
) -> None:
    """``open_archive``'s overloads, as each checker CI runs sees them.

    CI type-checks ``src/`` only, so the overloads' effect on a caller is checked here:
    every ``assert_type`` holds and the one diagnostic is ``forward.members()``. Both
    checkers, because the library is kept clean on both so that one's blind spot
    cannot hide what the other would catch.
    """
    exe = shutil.which(checker)
    if exe is None:
        pytest.skip(f"{checker} is not installed (it is a dev dependency)")
    (tmp_path / "sample.py").write_text(_OPEN_ARCHIVE_TYPING_SAMPLE)
    src = Path(archivey.__file__).resolve().parent.parent
    # Both checkers start the Python interpreter to find its search paths. pytest-cov
    # before 7 measures such a child through its COV_CORE_* variables, and from
    # tmp_path the child cannot find this repo's coverage config, so it writes
    # statement-only data that the branch-coverage parent then refuses to combine.
    env = {k: v for k, v in os.environ.items() if not k.startswith("COV_CORE_")}
    result = subprocess.run(
        _checker_command(checker, exe, tmp_path, src),
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
    )
    refused_line = _OPEN_ARCHIVE_TYPING_SAMPLE.splitlines().index(
        "        forward.members()"
    )
    output = result.stdout + result.stderr
    # ty prints ``sample.py:L:C: ...``; Pyrefly prints ``ERROR sample.py:L:C-C: ...``.
    diagnostics = [
        line.removeprefix("ERROR ")
        for line in output.splitlines()
        if line.startswith(("sample.py:", "ERROR sample.py:"))
    ]
    assert len(diagnostics) == 1, output
    assert diagnostics[0].startswith(f"sample.py:{refused_line + 1}:"), diagnostics
    assert "`members`" in diagnostics[0], diagnostics


def test_member_streams_is_demoted_from_the_surface(tmp_path) -> None:
    """The flag type behind ``seekable_members`` / ``concurrent_members`` stays internal.

    Pins the archive-reading spec: not re-exported, not in ``__all__``, still importable
    from ``archivey.types``, and no reader attribute exposes the declared flags.
    """
    from archivey.types import MemberStreams

    assert MemberStreams is not None
    assert "MemberStreams" not in archivey.__all__
    assert not hasattr(archivey, "MemberStreams")
    (tmp_path / "f.txt").write_bytes(b"x")
    with archivey.open_archive(tmp_path, seekable_members=True) as ar:
        assert not hasattr(ar, "member_streams")


def test_io_measurement_is_not_public() -> None:
    """IO counters serve the benchmark harness and the CLI's ``--track-io`` only.

    Not in ``__all__``, not on the package, not on the ``ArchiveReader`` ABC, and no
    public ``archivey.measurement`` module. The internal reader still answers.
    """
    import importlib.util

    from archivey.internal.base_reader import BaseArchiveReader

    for name in ("IoStats", "enable_measurement"):
        assert name not in archivey.__all__
        assert not hasattr(archivey, name)
    assert importlib.util.find_spec("archivey.measurement") is None
    assert "io_stats" not in vars(archivey.ArchiveReader)
    assert "io_stats" not in vars(archivey.StreamingArchiveReader)
    assert "io_stats" in vars(BaseArchiveReader)


def test_public_interface_hides_internal_hooks() -> None:
    """The public ``ArchiveReader`` surface must not expose backend-internal hooks.

    The concrete machinery (``_open_member`` etc.) lives on the internal
    ``BaseArchiveReader`` helper; the public interface declares only the public contract.
    """
    from archivey.internal.base_reader import BaseArchiveReader

    assert issubclass(BaseArchiveReader, archivey.ArchiveReader)
    internal_hooks = {
        "_iter_members",
        "_open_member",
        "_get_archive_info",
        "_close_archive",
    }
    public_names = set(vars(archivey.ArchiveReader)) | set(
        vars(archivey.StreamingArchiveReader)
    )
    leaked = internal_hooks & public_names
    assert not leaked, f"internal hooks leaked onto the public ArchiveReader: {leaked}"
    # They DO live on the internal helper.
    assert internal_hooks <= set(dir(BaseArchiveReader))


def test_all_entries_are_exported() -> None:
    """Every name in __all__ must actually be an attribute of the package."""
    missing = [name for name in archivey.__all__ if not hasattr(archivey, name)]
    assert not missing, f"__all__ lists names that are not exported: {missing}"


def test_all_has_no_duplicates() -> None:
    assert len(archivey.__all__) == len(set(archivey.__all__))


def test_public_symbols_are_in_all() -> None:
    """Imported public symbols (classes/functions, not modules or dunders) must be
    listed in __all__, so a new export can't be silently omitted.

    Names deliberately demoted from ``__all__`` but kept importable (Q4) are
    allowlisted here — they remain reachable as ``archivey.X`` for compatibility
    without advertising them as part of the curated public surface.
    """
    import inspect

    # Demoted from ``__all__`` but still imported at package level (api-coherence Q4).
    demoted_but_importable = {
        "ArchiveEofContext",
        "DigestContext",
        "EmptyArchiveContext",
        "EncryptedVerificationContext",
        "FormatConflictContext",
        "MemberHeaderRecordContext",
        "MemberNameControlsContext",
        "MemberTimestampContext",
        "NameEncodingContext",
        "NameNormalizationContext",
        "RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE",
        "ScanRaceContext",
        "SeekIndexContext",
        "SelectorUnmatchedContext",
        "StreamRewindContext",
        "SymlinkTargetContext",
        "UnconfirmedFormatContext",
        "UnusedArgumentContext",
    }

    public = {
        name
        for name, obj in vars(archivey).items()
        if not name.startswith("_")
        and not inspect.ismodule(obj)
        and name not in ("annotations",)
    }
    not_listed = public - set(archivey.__all__) - demoted_but_importable
    assert not not_listed, f"public symbols missing from __all__: {sorted(not_listed)}"
    # Demoted names must stay importable (do not silently drop the re-exports).
    missing_demoted = demoted_but_importable - public
    assert not missing_demoted, (
        f"demoted symbols no longer importable from archivey: {sorted(missing_demoted)}"
    )


def test_no_public_name_reports_an_internal_module() -> None:
    """Every class and function in ``__all__`` reports ``archivey``, not ``internal``.

    ``pickle`` stores ``__module__``, so a name reporting ``archivey.internal.…`` would
    freeze that path into data callers persist. ``__init__`` pins it for every name
    defined under ``internal``, including ones added later.
    """
    leaked = {
        name: obj.__module__
        for name in archivey.__all__
        if isinstance(obj := getattr(archivey, name), (type, FunctionType))
        if obj.__module__.startswith("archivey.internal")
    }
    assert leaked == {}


@pytest.mark.parametrize(
    "cls",
    [
        c
        for c in (getattr(archivey, n) for n in archivey.__all__)
        if isinstance(c, type)
    ],
    ids=lambda c: c.__name__,
)
def test_public_class_type_hints_resolve(cls: type) -> None:
    """``get_type_hints`` works on every public class, pinned or not.

    A pinned class's string hints would otherwise be looked up in ``archivey``, where
    names such as ``Path`` are not defined; ``__init__`` resolves them before the pin.
    """
    typing.get_type_hints(cls)


def test_pinned_class_source_lookup_fails_loudly() -> None:
    """``inspect.getsource`` cannot follow the pin; it raises rather than lie.

    Python 3.13+ locates a class by ``__firstlineno__`` in its module's file, which the
    pin turns into ``__init__.py``; ``__init__`` drops that attribute so the lookup
    raises ``OSError`` instead of returning unrelated lines.
    """
    with pytest.raises(OSError):
        inspect.getsource(archivey.ArchiveStream)


@pytest.mark.parametrize(
    "value",
    [
        archivey.OverwritePolicy.SKIP,
        archivey.DetectionConfidence.CERTAIN,
        archivey.FormatSupport.FULL,
    ],
    ids=lambda v: type(v).__name__,
)
def test_pickles_name_the_public_module(value: object) -> None:
    import pickle

    data = pickle.dumps(value)
    assert b"archivey.internal" not in data
    assert pickle.loads(data) is value


def test_pinning_leaves_the_internal_objects_shared() -> None:
    from archivey.internal.streams import archive_stream

    assert archive_stream.ArchiveStream is archivey.ArchiveStream
    assert archivey.ArchiveStream.__module__ == "archivey"


# ``ArchiveStream`` is an implementation class on the internal stream base; streams are
# never pickled, so it stays where it is and the pin covers it.
_PINNED_CLASSES = {"ArchiveStream"}


def test_public_classes_are_defined_in_public_modules() -> None:
    """A public class lives in a public module, so its ``__module__`` needs no pin.

    The pin in ``__init__`` makes a class defined under ``internal`` report
    ``archivey``, at the cost of ``inspect.getsource`` and a type-hint workaround. It is
    the safety net; this test keeps new public classes from leaning on it.
    """
    pinned = {
        name
        for name in archivey.__all__
        if isinstance(obj := getattr(archivey, name), type)
        and obj.__module__ == "archivey"
    }
    assert pinned == _PINNED_CLASSES


@pytest.mark.parametrize(
    "cls",
    [archivey.OverwritePolicy, archivey.ExtractionResult, archivey.FormatInfo],
    ids=lambda c: c.__name__,
)
def test_moved_classes_keep_their_source(cls: type) -> None:
    assert inspect.getsource(cls).lstrip().startswith(("class ", "@dataclass"))


def test_version_is_computed_on_first_access() -> None:
    code = (
        "import archivey\n"
        "assert '__version__' not in vars(archivey)\n"
        "v = archivey.__version__\n"
        "assert isinstance(v, str) and v, v\n"
        "assert vars(archivey)['__version__'] == v\n"
        "assert not hasattr(archivey, 'PackageNotFoundError')\n"
        "assert not hasattr(archivey, 'version')\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
    assert "__version__" in archivey.__all__


# Public names with no entry on the API page, each for a stated reason.
_NOT_ON_API_PAGE = {
    # The package version string: nothing to document beyond its name.
    "__version__",
}


def test_every_public_name_is_on_the_api_page() -> None:
    """``docs/api.md`` carries a ``::: archivey.<Name>`` block for each name in ``__all__``.

    The documentation spec says the API reference documents the public symbols
    re-exported from ``archivey.__all__``. Nothing in a docs build notices a name that
    was exported and never documented, and 29 had drifted off the page before this test.
    """
    api_page = Path(__file__).resolve().parent.parent / "docs" / "api.md"
    documented = {
        line.removeprefix("::: archivey.").strip()
        for line in api_page.read_text(encoding="utf-8").splitlines()
        if line.startswith("::: archivey.")
    }
    missing = sorted(set(archivey.__all__) - documented - _NOT_ON_API_PAGE)
    assert not missing, f"public names with no ::: block in docs/api.md: {missing}"
    stale = sorted(_NOT_ON_API_PAGE & documented)
    assert not stale, f"_NOT_ON_API_PAGE entries now documented, remove them: {stale}"
