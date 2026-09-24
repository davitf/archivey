"""Protocol conformance of the public types: hashing, pickling, copying.

``ArchiveMember`` must report itself unhashable to ``collections.abc.Hashable``, not
only fail on ``hash()``. Every exception class archivey exports must survive
``pickle`` and ``copy`` with its message and attributes intact, because an error
raised in a worker process crosses back to the caller pickled.
"""

from __future__ import annotations

import copy
import inspect
import pickle
from collections.abc import Hashable

import pytest

import archivey.exceptions as exceptions_module
from archivey.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    DiagnosticSeverity,
    EmptyArchiveContext,
)
from archivey.exceptions import (
    ArchiveyError,
    ArchiveyUsageError,
    DiagnosticRaisedError,
)
from archivey.types import ArchiveFormat, ArchiveMember, MemberType


def test_member_is_not_a_hashable_instance() -> None:
    member = ArchiveMember(type=MemberType.FILE, name="a")
    assert ArchiveMember.__hash__ is None
    assert not isinstance(member, Hashable)
    with pytest.raises(TypeError, match="unhashable type: 'ArchiveMember'"):
        hash(member)


def _diagnostic() -> Diagnostic:
    return Diagnostic(
        occurrence_id="0" * 32,
        code=DiagnosticCode.EMPTY_ARCHIVE,
        severity=DiagnosticSeverity.WARNING,
        message="Archive listed no members",
        context=EmptyArchiveContext(archive_name="x.zip", format="zip"),
    )


_EXCEPTION_CLASSES = sorted(
    (
        cls
        for _, cls in inspect.getmembers(exceptions_module, inspect.isclass)
        if issubclass(cls, (ArchiveyError, ArchiveyUsageError))
        and cls.__module__ == exceptions_module.__name__
    ),
    key=lambda cls: cls.__name__,
)


def _instance(cls: type[BaseException]) -> BaseException:
    # A control byte in the message, so a rebuild that re-escapes shows up.
    message = "bad \x1b[2K name"
    if issubclass(cls, ArchiveyUsageError):
        return cls(message)
    kwargs: dict[str, object] = {
        "archive_name": "a.zip",
        "member_name": "m\x1b",
        "link_target": "t",
        "source_format": ArchiveFormat.ZIP,
    }
    if issubclass(cls, DiagnosticRaisedError):
        kwargs["diagnostic"] = _diagnostic()
    else:
        kwargs["format_unconfirmed"] = True
    return cls(message, **kwargs)


def test_every_exception_class_is_covered() -> None:
    # The sweep below is only as good as its class list.
    assert DiagnosticRaisedError in _EXCEPTION_CLASSES
    assert len(_EXCEPTION_CLASSES) >= 25


@pytest.mark.parametrize("cls", _EXCEPTION_CLASSES, ids=lambda cls: cls.__name__)
@pytest.mark.parametrize(
    "roundtrip",
    [
        pytest.param(lambda exc: pickle.loads(pickle.dumps(exc)), id="pickle"),
        pytest.param(copy.copy, id="copy"),
        pytest.param(copy.deepcopy, id="deepcopy"),
    ],
)
def test_exception_roundtrips(cls: type[BaseException], roundtrip) -> None:
    original = _instance(cls)
    restored = roundtrip(original)
    assert type(restored) is cls
    assert restored.args == original.args
    assert str(restored) == str(original)
    assert vars(restored) == vars(original)


def test_diagnostic_raised_error_keeps_its_diagnostic_across_pickle() -> None:
    original = _instance(DiagnosticRaisedError)
    assert isinstance(original, DiagnosticRaisedError)
    restored = pickle.loads(pickle.dumps(original))
    assert restored.diagnostic == original.diagnostic
    assert restored.raw_message == original.raw_message == "bad \x1b[2K name"
    assert restored.message == original.message == "bad \\x1b[2K name"
