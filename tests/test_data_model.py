"""Archive-data-model contract tests (phase-5 task 6.3 audit).

Pure data-type behaviours required by ``archive-data-model`` that the per-format tests
don't exercise directly: ``ArchiveFormat`` identity (compositional round-trip, on-demand
construction of uncommon container×codec pairs, ``file_extension``) and the
``ArchiveMember`` value-object contract (unhashable, copy-on-edit via ``replace``,
``None`` defaults, equality excluding hashes/extra, and the type helpers).
"""

from __future__ import annotations

import ast
import copy
import json
import pickle
from pathlib import Path
from typing import Literal, get_args, get_origin, get_overloads, get_type_hints

import pytest

from archivey.types import (
    EXTRA_IS_JUNCTION,
    EXTRA_RAR_CREATED_IS_CTIME,
    EXTRA_RAR_EXTRACT_VERSION,
    ArchiveFormat,
    ArchiveInfo,
    ArchiveInfoExtra,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    ContainerFormat,
    HashAlgorithm,
    MemberExtra,
    MemberType,
    StreamFormat,
    crc32_digest,
)

# ---------------------------------------------------------------------------
# ArchiveFormat identity (compositional (container, stream) model)
# ---------------------------------------------------------------------------


def test_format_identity_round_trips_through_pair() -> None:
    assert ArchiveFormat(ContainerFormat.TAR, StreamFormat.GZIP) == ArchiveFormat.TAR_GZ


def test_standalone_lzip_has_named_format() -> None:
    assert ArchiveFormat.LZIP.container == ContainerFormat.RAW_STREAM
    assert ArchiveFormat.LZIP.stream == StreamFormat.LZIP


def test_standalone_lzma_alone_has_named_format() -> None:
    assert ArchiveFormat.LZMA_ALONE.container == ContainerFormat.RAW_STREAM
    assert ArchiveFormat.LZMA_ALONE.stream == StreamFormat.LZMA_ALONE
    assert ArchiveFormat.LZMA_ALONE.file_extension() == "lzma"
    assert StreamFormat.LZMA_ALONE.value == "lzma"


def test_uncommon_container_codec_built_on_demand() -> None:
    # tar.lz has no predefined TAR_LZIP constant, but is constructed on demand and compares
    # equal to any other instance with the same (container, stream) pair.
    fmt = ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP)
    assert fmt == ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP)
    assert fmt.file_extension() == "tar.lz"


def test_tar_lzma_alone_built_on_demand() -> None:
    fmt = ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZMA_ALONE)
    assert fmt.file_extension() == "tar.lzma"


def test_file_extension_examples() -> None:
    assert ArchiveFormat.ZIP.file_extension() == "zip"
    assert ArchiveFormat.TAR_GZ.file_extension() == "tar.gz"
    assert ArchiveFormat.GZ.file_extension() == "gz"
    # Formats with no on-disk file representation return "".
    assert ArchiveFormat.DIRECTORY.file_extension() == ""
    assert ArchiveFormat.UNKNOWN.file_extension() == ""


# ---------------------------------------------------------------------------
# ArchiveMember value-object contract
# ---------------------------------------------------------------------------


def test_member_is_unhashable() -> None:
    m = ArchiveMember(type=MemberType.FILE, name="a.txt")
    with pytest.raises(TypeError):
        hash(m)
    with pytest.raises(TypeError):
        _ = {m}  # set membership needs hashing → unhashable


def test_replace_returns_copy_without_mutating_original() -> None:
    m = ArchiveMember(type=MemberType.FILE, name="a.txt", mode=0o644)
    copy = m.replace(name="b.txt")
    assert copy is not m
    assert copy.name == "b.txt"
    assert copy.mode == 0o644  # untouched fields carried over
    assert m.name == "a.txt"  # original never mutated


def test_unavailable_fields_default_to_none() -> None:
    # The library must not substitute silent defaults for fields a format cannot provide.
    m = ArchiveMember(type=MemberType.FILE, name="a.txt")
    assert m.size is None
    assert m.compressed_size is None
    assert m.mode is None
    assert m.modified is None
    assert m.link_target is None
    assert m.link_target_member is None
    assert m.compression == ()


def test_equality_excludes_hashes_and_extra() -> None:
    # hashes vary by format and extra is format-specific overflow; neither affects logical
    # identity, so both are excluded from __eq__.
    a = ArchiveMember(
        type=MemberType.FILE,
        name="a.txt",
        hashes={HashAlgorithm.CRC32: crc32_digest(1)},
        extra=MemberExtra({"x": 1}),
    )
    b = ArchiveMember(
        type=MemberType.FILE,
        name="a.txt",
        hashes={HashAlgorithm.CRC32: crc32_digest(2)},
        extra=MemberExtra({"y": 2}),
    )
    assert a == b


def test_anti_and_current_defaults_and_equality() -> None:
    # Defaults: ordinary members are non-anti and current.
    default = ArchiveMember(type=MemberType.FILE, name="a.txt")
    assert default.is_anti is False
    assert default.is_current is True

    # is_anti is derived from type; is_current remains a field in equality.
    anti = ArchiveMember(type=MemberType.ANTI, name="a.txt")
    assert anti.is_anti is True
    assert anti.is_file is False
    assert anti != default
    superseded = ArchiveMember(type=MemberType.FILE, name="a.txt", is_current=False)
    assert superseded != default
    assert anti == ArchiveMember(type=MemberType.ANTI, name="a.txt")


def test_single_codec_member_compression_shape() -> None:
    m = ArchiveMember(
        type=MemberType.FILE,
        name="a",
        compression=(CompressionMethod(CompressionAlgorithm.DEFLATE),),
    )
    assert m.compression == (CompressionMethod(algo=CompressionAlgorithm.DEFLATE),)


def test_type_helpers() -> None:
    assert ArchiveMember(type=MemberType.FILE, name="f").is_file
    assert ArchiveMember(type=MemberType.DIRECTORY, name="d/").is_dir
    assert ArchiveMember(type=MemberType.SYMLINK, name="s").is_link
    assert ArchiveMember(type=MemberType.HARDLINK, name="h").is_link
    assert ArchiveMember(type=MemberType.OTHER, name="o").is_other
    assert ArchiveMember(type=MemberType.ANTI, name="a").is_anti
    assert not ArchiveMember(type=MemberType.ANTI, name="a").is_file


def test_junction_helper() -> None:
    junction = ArchiveMember(
        type=MemberType.SYMLINK, name="j", extra=MemberExtra({EXTRA_IS_JUNCTION: True})
    )
    assert junction.is_junction
    assert not ArchiveMember(type=MemberType.SYMLINK, name="s").is_junction


def test_extra_bags_are_runtime_dict_subclasses() -> None:
    # The names are importable; a core install has no typing_extensions.
    assert ArchiveMember.__annotations__["extra"] == "MemberExtra"
    assert ArchiveInfo.__annotations__["extra"] == "ArchiveInfoExtra"
    assert issubclass(MemberExtra, dict)
    assert issubclass(ArchiveInfoExtra, dict)

    default = ArchiveMember(type=MemberType.FILE, name="a")
    assert isinstance(default.extra, MemberExtra)
    default.extra["third.party"] = 1
    assert default.extra["third.party"] == 1

    e = MemberExtra({"is_junction": True, "third.party": 1})
    assert e == {"is_junction": True, "third.party": 1}
    assert json.dumps(e, sort_keys=True)
    assert copy.copy(e) == e
    assert copy.deepcopy(e) == e
    assert pickle.loads(pickle.dumps(e)) == e


def test_extra_key_register_matches_write_sites() -> None:
    """Overload keys stay in lockstep with production writes (K5).

    ``typing.get_overloads`` is the runtime register. Production write sites
    (``MemberExtra({...})`` / ``ArchiveInfoExtra({...})`` constructors, and
    ``extra[key]`` / ``member.extra[key]`` / ``info_extra[key]`` assignments)
    must match it. Mutation that fails this: deleting the
    ``zip.compress_type`` overload while the ZIP backend still writes that
    key. ``synthetic.header_len`` is a test-only key
    (``tests/test_codec_descriptor.py``) and must stay off the register.
    """
    src_root = Path(__file__).resolve().parent.parent / "src" / "archivey"
    extra_consts = {
        "EXTRA_IS_JUNCTION": EXTRA_IS_JUNCTION,
        "EXTRA_RAR_CREATED_IS_CTIME": EXTRA_RAR_CREATED_IS_CTIME,
        "EXTRA_RAR_EXTRACT_VERSION": EXTRA_RAR_EXTRACT_VERSION,
    }

    def keys_from_overloads(cls: type) -> set[str]:
        keys: set[str] = set()
        saw_fallback = False
        for fn in get_overloads(cls.__getitem__):
            hints = get_type_hints(fn)
            key_type = hints["key"]
            origin = get_origin(key_type)
            args = get_args(key_type)
            if origin is Literal:
                for arg in args:
                    assert isinstance(arg, str)
                    keys.add(arg)
            elif key_type is str:
                saw_fallback = True
            else:
                raise AssertionError(f"unexpected key annotation {key_type!r}")
        assert saw_fallback, f"{cls.__name__} is missing the str → object fallback"
        return keys

    def literal_or_const(node: ast.AST | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id in extra_consts:
            return extra_consts[node.id]
        return None

    def bag_kind(value: ast.AST) -> str | None:
        if isinstance(value, ast.Name):
            if value.id == "extra":
                return "member"
            if value.id == "info_extra":
                return "archive"
        if isinstance(value, ast.Attribute) and value.attr == "extra":
            return "member"
        return None

    written_member: set[str] = set()
    written_archive: set[str] = set()
    for path in src_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"MemberExtra", "ArchiveInfoExtra"} and node.args:
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Dict):
                        target = (
                            written_member
                            if node.func.id == "MemberExtra"
                            else written_archive
                        )
                        for k in arg0.keys:
                            key = literal_or_const(k)
                            if key is not None:
                                target.add(key)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if not isinstance(target, ast.Subscript):
                        continue
                    key = literal_or_const(target.slice)
                    if key is None:
                        continue
                    bag = bag_kind(target.value)
                    if bag == "member":
                        written_member.add(key)
                    elif bag == "archive":
                        written_archive.add(key)

    member_keys = keys_from_overloads(MemberExtra)
    archive_keys = keys_from_overloads(ArchiveInfoExtra)
    assert member_keys == written_member, (
        f"MemberExtra overloads {sorted(member_keys)} != "
        f"production writes {sorted(written_member)}"
    )
    assert archive_keys == written_archive, (
        f"ArchiveInfoExtra overloads {sorted(archive_keys)} != "
        f"production writes {sorted(written_archive)}"
    )
    assert "synthetic.header_len" not in member_keys


def test_modified_utc_normalizes_mixed_timestamps() -> None:
    from datetime import datetime, timedelta, timezone

    # Aware (e.g. an NTFS-extra UTC time): converted to UTC.
    aware = ArchiveMember(
        type=MemberType.FILE,
        name="aware",
        modified=datetime(2020, 6, 1, 14, 0, tzinfo=timezone(timedelta(hours=2))),
    )
    assert aware.modified_utc() == datetime(2020, 6, 1, 12, 0, tzinfo=timezone.utc)

    # Naive (a wall-clock DOS time): tz_for_naive supplies the caller's assumption.
    naive = ArchiveMember(
        type=MemberType.FILE, name="naive", modified=datetime(2020, 6, 1, 14, 0)
    )
    as_utc = naive.modified_utc(tz_for_naive=timezone(timedelta(hours=-3)))
    assert as_utc == datetime(2020, 6, 1, 17, 0, tzinfo=timezone.utc)

    # Default: naive is interpreted in the local timezone; the result is aware UTC and
    # comparable with the aware member's (mixed naive/aware raises TypeError directly).
    local_utc = naive.modified_utc()
    assert local_utc is not None and local_utc.tzinfo == timezone.utc
    assert (local_utc < aware.modified_utc()) in (
        True,
        False,
    )  # comparable, no TypeError

    # The stored field is untouched: provenance stays checkable.
    assert naive.modified is not None and naive.modified.tzinfo is None
    assert ArchiveMember(type=MemberType.FILE, name="none").modified_utc() is None
