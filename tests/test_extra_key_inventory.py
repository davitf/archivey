"""The extra-bag overload list matches the keys backends actually write.

``extra_items`` / a fallback ``str`` overload accept any misspelled key, so
nothing in the type itself keeps the register complete. This test is that
guard: ``typing.get_overloads`` minus the ``str -> object`` fallback must equal
the keys written under ``src/archivey/``. Deleting one known-key overload
turns it red. Test-only keys (``synthetic.header_len``) stay on an allowlist.
"""

from __future__ import annotations

import ast
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


def _literal_keys(mapping_cls: type) -> set[str]:
    keys: set[str] = set()
    for fn in typing.get_overloads(mapping_cls.__getitem__):
        hints = typing.get_type_hints(fn)
        key_ann = hints.get("key")
        if key_ann is str:
            continue
        for arg in typing.get_args(key_ann):
            if isinstance(arg, str):
                keys.add(arg)
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


def _written_keys(root: Path) -> tuple[set[str], set[str]]:
    member: set[str] = set()
    info: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and _is_extra_target(node.value):
                key = _key_from_slice(node.slice)
                if key is None:
                    continue
                if isinstance(node.value, ast.Name) and node.value.id == "info_extra":
                    info.add(key)
                else:
                    member.add(key)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if not node.args:
                    continue
                arg0 = node.args[0]
                if not isinstance(arg0, ast.Dict):
                    continue
                if node.func.id == "MemberExtra":
                    member |= _keys_from_dict(arg0)
                elif node.func.id == "ArchiveInfoExtra":
                    info |= _keys_from_dict(arg0)
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
