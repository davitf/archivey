"""Guards on the public ``archivey`` namespace.

``archivey.__all__`` is curated by hand (not generated) so it lists only the public
API and not re-imported helpers like ``version``/``PackageNotFoundError``. This test
is the safety net that keeps the hand-maintained list from drifting.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
import typing
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
    public_names = set(vars(archivey.ArchiveReader))
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
        "WriteError",
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
        if isinstance(c, type) and c.__module__ == "archivey"
    ],
    ids=lambda c: c.__name__,
)
def test_public_class_type_hints_resolve(cls: type) -> None:
    """``get_type_hints`` works on every public class, pinned ones included.

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
        inspect.getsource(archivey.OverwritePolicy)


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
    from archivey.internal import extraction_types

    assert extraction_types.OverwritePolicy is archivey.OverwritePolicy
    assert archivey.ExtractionResult.__module__ == "archivey"


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
