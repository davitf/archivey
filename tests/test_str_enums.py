"""Public string enums return their value from ``str()`` and f-strings.

On Python 3.11 and later, a ``(str, Enum)`` member renders as ``Class.NAME``.
``StrEnum`` still subclasses ``str``, still compares equal to its value, and
``str()`` and f-strings return that value.

These classes are ``StrEnum``: ``HashAlgorithm``, ``ContainerFormat``,
``StreamFormat``, ``DiagnosticCode``, ``DiagnosticDisposition``, ``AbortOn``,
``ExtractionStatus``. The source scan fails if another class inherits
``(str, Enum)``.
"""

from __future__ import annotations

import ast
import json
import pickle
from enum import StrEnum
from pathlib import Path

import pytest

from archivey import (
    AbortOn,
    ContainerFormat,
    DiagnosticCode,
    DiagnosticDisposition,
    ExtractionStatus,
    HashAlgorithm,
    StreamFormat,
)

_SRC = Path(__file__).resolve().parents[1] / "src" / "archivey"

# The public string enums. ``str()`` / f-strings return the value, and a
# mapping keyed by the member still accepts the value string.
STR_VALUE_ENUMS: tuple[type[StrEnum], ...] = (
    HashAlgorithm,
    ContainerFormat,
    StreamFormat,
    DiagnosticCode,
    DiagnosticDisposition,
    AbortOn,
    ExtractionStatus,
)


def _base_label(base: ast.expr) -> str | None:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
        return f"{base.value.id}.{base.attr}"
    return None


def _enum_bases(path: Path) -> list[tuple[str, int, tuple[str, ...]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        labels = tuple(
            label for base in node.bases if (label := _base_label(base)) is not None
        )
        if labels:
            found.append((node.name, node.lineno, labels))
    return found


def _is_str_enum_mixin(labels: tuple[str, ...]) -> bool:
    return "str" in labels and ("Enum" in labels or "enum.Enum" in labels)


def _is_strenum(labels: tuple[str, ...]) -> bool:
    return "StrEnum" in labels or "enum.StrEnum" in labels


@pytest.mark.parametrize("enum_cls", STR_VALUE_ENUMS, ids=lambda cls: cls.__name__)
def test_str_and_format_return_the_value(enum_cls: type[StrEnum]) -> None:
    assert enum_cls.__members__, f"{enum_cls.__name__} has no members"
    for member in enum_cls:
        assert str(member) == member.value
        assert f"{member}" == member.value
        assert format(member) == member.value
        assert member == member.value
        assert isinstance(member, str)
        assert hash(member) == hash(member.value)
        assert {member: b"\x00"}[member.value] == b"\x00"
        assert json.dumps(member) == json.dumps(member.value)
        assert pickle.loads(pickle.dumps(member)) is member
        assert repr(member) == (
            f"<{enum_cls.__name__}.{member.name}: {member.value!r}>"
        )
    assert issubclass(enum_cls, StrEnum)


def test_source_uses_strenum_for_string_enums() -> None:
    """No ``(str, Enum)`` class, and every ``StrEnum`` is one of the seven."""
    mixin: list[str] = []
    strenums: set[str] = set()
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC.parent.parent).as_posix()
        for name, lineno, labels in _enum_bases(path):
            if _is_str_enum_mixin(labels):
                mixin.append(f"{rel}:{lineno}: {name}({', '.join(labels)})")
            if _is_strenum(labels):
                strenums.add(name)
    assert mixin == []
    assert strenums == {cls.__name__ for cls in STR_VALUE_ENUMS}
