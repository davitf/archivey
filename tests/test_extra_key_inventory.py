"""The extra-bag overload list matches the keys backends actually write.

The ``str → object`` fallback accepts any misspelled key, so nothing in the
type itself keeps the register complete. This test is that guard:
``typing.get_overloads`` minus the fallback must equal the keys written under
``src/archivey/``. Deleting one known-key overload (the check used
``zip.compress_type``) turns it red. Test-only keys (``synthetic.header_len``)
stay on an allowlist.

The writer scan covers the naming convention the backends use: a
``MemberExtra(...)`` / ``ArchiveInfoExtra(...)`` constructor, a subscript of a
name assigned from that constructor, or a subscript of ``extra`` /
``info_extra`` / ``.extra``. A constructor whose first argument is not a dict
literal is an error rather than a silent skip. A new site has to follow that
convention.

The ``Known keys:`` bullets in each class docstring are the published copy
(``docs/formats.md`` points at them). Those keys and type strings must match
the overload register; adding an overload without its bullet turns it red.
"""

from __future__ import annotations

import ast
import re
import typing
from pathlib import Path

from archivey.types import (
    EXTRA_IS_JUNCTION,
    EXTRA_RAR_CREATED_IS_CTIME,
    EXTRA_RAR_EXTRACT_VERSION,
    ArchiveInfoExtra,
    MemberExtra,
)

REPO_SRC = Path(__file__).resolve().parents[1] / "src" / "archivey"
REPO_TESTS = Path(__file__).resolve().parents[0]

_CONST_KEYS = {
    "EXTRA_IS_JUNCTION": EXTRA_IS_JUNCTION,
    "EXTRA_RAR_CREATED_IS_CTIME": EXTRA_RAR_CREATED_IS_CTIME,
    "EXTRA_RAR_EXTRACT_VERSION": EXTRA_RAR_EXTRACT_VERSION,
}

# Written by tests, not by the library. See tests/test_codec_descriptor.py
# (`synthetic.header_len`) and tests/test_data_model.py (open-bag example).
_TEST_ONLY_MEMBER_KEYS = frozenset({"synthetic.header_len", "third.party"})

_DOC_BULLET = re.compile(r"^\s*\* ``([^`]+)`` \(``([^`]+)``\)(?: — .*)?$", re.MULTILINE)


def _ann_str(ann: object) -> str:
    if isinstance(ann, type) and ann.__module__ == "builtins":
        return ann.__name__
    origin = typing.get_origin(ann)
    if origin is dict:
        return "dict[" + ", ".join(_ann_str(a) for a in typing.get_args(ann)) + "]"
    raise AssertionError(f"unrenderable annotation {ann!r}")


def _literal_keys(mapping_cls: type) -> set[str]:
    return set(_overload_register(mapping_cls))


def _overload_register(mapping_cls: type) -> dict[str, str]:
    keys: dict[str, str] = {}
    saw_fallback = False
    for fn in typing.get_overloads(mapping_cls.__getitem__):
        hints = typing.get_type_hints(fn)
        key_ann = hints.get("key")
        if key_ann is str:
            saw_fallback = True
            continue
        origin = typing.get_origin(key_ann)
        if origin is not typing.Literal:
            raise AssertionError(
                f"{mapping_cls.__name__} has unexpected key annotation {key_ann!r}"
            )
        ret = _ann_str(hints["return"])
        for arg in typing.get_args(key_ann):
            if isinstance(arg, str):
                keys[arg] = ret
    assert saw_fallback, f"{mapping_cls.__name__} is missing the str → object fallback"
    return keys


def _docstring_register(mapping_cls: type) -> dict[str, str]:
    doc = mapping_cls.__doc__
    assert doc is not None, f"{mapping_cls.__name__} has no docstring"
    marker = "Known keys:"
    idx = doc.find(marker)
    assert idx >= 0, f"{mapping_cls.__name__} docstring has no Known keys: section"
    entries = _DOC_BULLET.findall(doc[idx + len(marker) :])
    assert entries, f"{mapping_cls.__name__} Known keys: section is empty"
    keys = dict(entries)
    assert len(keys) == len(entries), f"{mapping_cls.__name__} docstring repeats a key"
    return keys


def _is_extra_target(node: ast.AST) -> bool:
    if isinstance(node, ast.Name) and node.id in {"extra", "info_extra"}:
        return True
    return isinstance(node, ast.Attribute) and node.attr == "extra"


def _key_from_slice(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONST_KEYS:
        return _CONST_KEYS[node.id]
    return None


def _keys_from_dict(node: ast.Dict) -> set[str]:
    keys: set[str] = set()
    for key in node.keys:
        if key is None:
            continue
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
        elif isinstance(key, ast.Name) and key.id in _CONST_KEYS:
            keys.add(_CONST_KEYS[key.id])
    return keys


def _bag_kind_from_call(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name) and func.id in {"MemberExtra", "ArchiveInfoExtra"}:
        return "member" if func.id == "MemberExtra" else "info"
    return None


def _record_constructor(
    node: ast.Call, path: Path, member: set[str], info: set[str]
) -> str | None:
    kind = _bag_kind_from_call(node.func)
    if kind is None:
        return None
    if node.args:
        arg0 = node.args[0]
        if not isinstance(arg0, ast.Dict):
            raise AssertionError(
                f"{path.as_posix()}: {ast.unparse(node.func)}(...) first arg "
                "is not a dict literal"
            )
        keys = _keys_from_dict(arg0)
        if kind == "member":
            member |= keys
        else:
            info |= keys
    return kind


def _written_keys(root: Path) -> tuple[set[str], set[str]]:
    member: set[str] = set()
    info: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        name_bag: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                    kind = _record_constructor(node.value, path, member, info)
                    if kind is not None:
                        name_bag[target.id] = kind
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if isinstance(node.value, ast.Call):
                    kind = _record_constructor(node.value, path, member, info)
                    if kind is not None:
                        name_bag[node.target.id] = kind
            elif isinstance(node, ast.Call):
                # Constructors not bound to a name (still need the raise-on-skip).
                if not (
                    isinstance(node.func, ast.Name)
                    and node.func.id in {"MemberExtra", "ArchiveInfoExtra"}
                ):
                    continue
                # Assignment/AnnAssign already recorded these; recording twice is
                # idempotent for the key sets.
                _record_constructor(node, path, member, info)
            elif isinstance(node, ast.Subscript):
                key = _key_from_slice(node.slice)
                if key is None:
                    continue
                bag: str | None = None
                if isinstance(node.value, ast.Name):
                    bag = name_bag.get(node.value.id)
                    if bag is None:
                        if node.value.id == "info_extra":
                            bag = "info"
                        elif node.value.id == "extra":
                            bag = "member"
                elif _is_extra_target(node.value):
                    bag = "member"
                if bag == "info":
                    info.add(key)
                elif bag == "member":
                    member.add(key)
    return member, info


def test_member_extra_overloads_match_src_writers() -> None:
    declared = _literal_keys(MemberExtra)
    written, _ = _written_keys(REPO_SRC)
    assert written == declared, (
        f"undeclared writes {sorted(written - declared)}; "
        f"unused overloads {sorted(declared - written)}"
    )


def test_archive_info_extra_overloads_match_src_writers() -> None:
    declared = _literal_keys(ArchiveInfoExtra)
    _, written = _written_keys(REPO_SRC)
    assert written == declared, (
        f"undeclared writes {sorted(written - declared)}; "
        f"unused overloads {sorted(declared - written)}"
    )


def test_tests_do_not_invent_undeclared_extra_keys() -> None:
    declared = _literal_keys(MemberExtra) | _literal_keys(ArchiveInfoExtra)
    written_member, written_info = _written_keys(REPO_TESTS)
    unexpected = (written_member | written_info) - declared - _TEST_ONLY_MEMBER_KEYS
    assert not unexpected, (
        f"test-only extra keys missing from allowlist: {sorted(unexpected)}"
    )


def test_docstring_known_keys_match_overloads() -> None:
    for cls in (MemberExtra, ArchiveInfoExtra):
        assert _docstring_register(cls) == _overload_register(cls), cls.__name__
