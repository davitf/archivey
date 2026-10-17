"""Non-file open/read contract: directories and ANTI raise ArchiveyUsageError, and a
directory that declares data gives the same bytes through ``open()`` and through
``stream_members()``."""

from __future__ import annotations

import io
import struct
import tarfile
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest

from archivey import ArchiveReader, DiagnosticCode, open_archive
from archivey.exceptions import ArchiveyError, ArchiveyUsageError, CorruptionError
from archivey.types import MemberType
from tests.conftest import requires, requires_binary
from tests.streams_util import NonSeekableBytesIO


def _assert_dir_open_raises(archive_path: Path) -> None:
    with open_archive(archive_path) as reader:
        directory = next(m for m in reader.members() if m.is_dir)
        with pytest.raises(ArchiveyUsageError, match="not a file"):
            reader.open(directory)
        with pytest.raises(ArchiveyUsageError, match="not a file"):
            reader.read(directory)
        for member, stream in reader.stream_members():
            if member.is_dir:
                assert stream is None
            elif stream is not None:
                stream.close()


def test_zip_directory_open_raises(tmp_path: Path) -> None:
    archive = tmp_path / "dirs.zip"
    with ZipFile(archive, "w") as zf:
        zf.writestr("adir/", "")
        zf.writestr("adir/f.txt", b"hi")
    _assert_dir_open_raises(archive)


def test_tar_directory_open_raises(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    (root / "adir").mkdir(parents=True)
    (root / "adir" / "f.txt").write_bytes(b"hi")
    archive = tmp_path / "dirs.tar"
    with tarfile.open(archive, "w") as tf:
        tf.add(root / "adir", arcname="adir")
    _assert_dir_open_raises(archive)


def test_directory_backend_open_raises(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "f.txt").write_bytes(b"x")
    _assert_dir_open_raises(root)


@requires("pycdlib")
def test_iso_directory_open_raises(tmp_path: Path) -> None:
    from tests.test_iso import _build_iso

    path = tmp_path / "rr.iso"
    path.write_bytes(_build_iso(rock_ridge=True, joliet=True))
    with open_archive(path) as reader:
        directory = next(m for m in reader.members() if m.is_dir)
        assert directory.type is MemberType.DIRECTORY
        with pytest.raises(ArchiveyUsageError, match="not a file"):
            reader.read(directory)


# --- a directory entry that declares data: the same bytes in both access modes ---------

# What one member gives: its bytes, ``None`` when there is nothing to deliver (the pass
# yields no stream; ``open()`` refuses with "not a file"), or the class of the error
# reading it raises.
_Outcome = bytes | None | type[BaseException]


def _zip_directory_data(tmp_path: Path, shape: str) -> Path:
    """``d/`` carrying 12 bytes, a file under it, and ``e/`` with no data."""
    path = tmp_path / f"{shape}.zip"
    method = zipfile.ZIP_DEFLATED if shape == "zip-deflate" else zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("d/"), b"hidden data!", compress_type=method)
        zf.writestr("d/f.txt", b"visible")
        zf.writestr(zipfile.ZipInfo("e/"), b"")
    data = bytearray(path.read_bytes())
    central = data.index(b"PK\x01\x02")
    if shape == "zip-zero-crc":
        # A directory's conventional CRC-32 field: no digest, so DIGEST_UNVERIFIABLE.
        struct.pack_into("<I", data, 14, 0)
        struct.pack_into("<I", data, central + 16, 0)
    elif shape == "zip-bad-crc":
        assert data.count(b"hidden data!") == 1
        data = bytearray(data.replace(b"hidden data!", b"XXXXXXXXXXXX"))
    path.write_bytes(bytes(data))
    return path


def _tar_directory_data(tmp_path: Path, shape: str) -> Path:
    from tests.test_tar import _tar_slash_entry_with_data

    if shape == "tar-dirtype-size":
        # A DIRTYPE header with a size and no blocks behind it: listed with no size,
        # so open() refuses it and the pass yields no stream.
        data = _tar_slash_entry_with_data(
            tarfile.DIRTYPE, data=b"", size_field=b"%011o\0" % 4096
        )
    else:
        typeflag = tarfile.AREGTYPE if shape == "tar-old-style-nul" else tarfile.REGTYPE
        data = _tar_slash_entry_with_data(typeflag)
    path = tmp_path / f"{shape}.tar"
    path.write_bytes(data)
    return path


def _rar_directory_data(tmp_path: Path, shape: str) -> Path:
    from tests.test_audit_rar_iso_dir import (
        _FIXTURES,
        _rar5_build,
        _rar5_file_blocks,
        _rar5_parse,
        _rar_with_directory_data,
    )

    if shape != "rar-solid-stored":
        return _rar_with_directory_data(tmp_path, stored=shape == "rar-stored")
    # The solid fixture's last entry is the directory ``subdir``: give it a stored
    # body. It comes after every file, so no file's solid stream depends on it.
    blocks = _rar5_parse((_FIXTURES / "basic_solid__.rar").read_bytes())
    last = _rar5_file_blocks(blocks)[-1]
    assert last["name"] == b"subdir" and last["file_flags"] & 1
    last["data"] = b"stored dir data"
    last["unpacked"] = len(last["data"])
    last["crc"] = struct.pack("<I", zlib.crc32(last["data"]))
    last["cinfo"] = 0
    path = tmp_path / "solid_dir.rar"
    path.write_bytes(_rar5_build(blocks))
    return path


def _sevenzip_directory(tmp_path: Path, shape: str) -> Path:
    # 7z has no directory with data: a record with a stream is a FILE (DR-25), so
    # the directory here has none, and both modes give it nothing.
    import py7zr

    tree = tmp_path / "tree" / "sub"
    tree.mkdir(parents=True)
    (tree / "f.txt").write_bytes(b"hi")
    path = tmp_path / "dir.7z"
    with py7zr.SevenZipFile(path, "w") as zf:
        zf.writeall(tree, "sub")
    return path


def _random_access_outcomes(path: Path) -> dict[str, _Outcome]:
    outcomes: dict[str, _Outcome] = {}
    with open_archive(path) as reader:
        for member in reader.members():
            try:
                outcomes[member.name] = reader.read(member)
            except ArchiveyUsageError as exc:
                assert "not a file" in str(exc)
                outcomes[member.name] = None
            except ArchiveyError as exc:
                outcomes[member.name] = type(exc)
    return outcomes


def _pass_outcomes(reader: ArchiveReader) -> dict[str, _Outcome]:
    outcomes: dict[str, _Outcome] = {}
    for member, stream in reader.stream_members():
        if stream is None:
            outcomes[member.name] = None
            continue
        try:
            outcomes[member.name] = stream.read()
        except ArchiveyError as exc:
            outcomes[member.name] = type(exc)
    return outcomes


_SHAPES: dict[str, tuple[Callable[[Path, str], Path], dict[str, _Outcome]]] = {
    "zip-stored": (
        _zip_directory_data,
        {"d/": b"hidden data!", "d/f.txt": b"visible", "e/": None},
    ),
    "zip-deflate": (
        _zip_directory_data,
        {"d/": b"hidden data!", "d/f.txt": b"visible", "e/": None},
    ),
    "zip-zero-crc": (
        _zip_directory_data,
        {"d/": b"hidden data!", "d/f.txt": b"visible", "e/": None},
    ),
    "zip-bad-crc": (
        _zip_directory_data,
        {"d/": CorruptionError, "d/f.txt": b"visible", "e/": None},
    ),
    "tar-old-style-nul": (
        _tar_directory_data,
        {"d/": b"hello directory", "after.txt": b"after\n"},
    ),
    "tar-old-style-regtype": (
        _tar_directory_data,
        {"d/": b"hello directory", "after.txt": b"after\n"},
    ),
    "tar-dirtype-size": (_tar_directory_data, {"d/": None, "after.txt": b"after\n"}),
    "7z": (_sevenzip_directory, {"sub/": None, "sub/f.txt": b"hi"}),
}
_RAR_SHAPES = ("rar-stored", "rar-compressed", "rar-solid-stored")


def _shape_params() -> list[Any]:
    params: list[Any] = [
        pytest.param(shape, marks=requires("py7zr")) if shape == "7z" else shape
        for shape in _SHAPES
    ]
    params += [
        pytest.param(shape, marks=requires_binary("unrar")) for shape in _RAR_SHAPES
    ]
    return params


def _build(tmp_path: Path, shape: str) -> Path:
    if shape in _RAR_SHAPES:
        return _rar_directory_data(tmp_path, shape)
    return _SHAPES[shape][0](tmp_path, shape)


@pytest.mark.parametrize("shape", _shape_params())
def test_directory_data_is_the_same_in_a_pass_and_in_random_access(
    tmp_path: Path, shape: str
) -> None:
    """``stream_members()`` yields a stream for exactly the directories ``open()``
    reads, with the same bytes and the same checks: a CRC that fails raises in both,
    data archivey cannot decode is refused in both, a directory with no data (or a
    TAR ``DIRTYPE`` size that cannot be told from the trailer) gets nothing in both.
    The pass is checked on a random-access reader and, where the format has one, on
    a forward-only reader of a source that cannot seek."""
    path = _build(tmp_path, shape)
    expected = _random_access_outcomes(path)
    if shape in _SHAPES:
        assert expected == _SHAPES[shape][1]
    with open_archive(path) as reader:
        assert _pass_outcomes(reader) == expected
    if shape.startswith(("zip", "tar")):
        with open_archive(
            NonSeekableBytesIO(path.read_bytes()), streaming=True
        ) as reader:
            assert _pass_outcomes(reader) == expected
    else:
        with open_archive(path, streaming=True) as reader:
            assert _pass_outcomes(reader) == expected


@pytest.mark.parametrize("streaming", [False, True], ids=["seekable", "pipe"])
def test_zip_directory_data_digest_unverifiable_in_a_pass(
    tmp_path: Path, streaming: bool
) -> None:
    """A ZIP directory whose CRC-32 field is 0 over declared data reads unchecked, and
    says so with ``DIGEST_UNVERIFIABLE``, from a pass as from ``open()``. A pipe
    reports both codes once the central directory arrives, at the end of the pass."""
    data = _zip_directory_data(tmp_path, "zip-zero-crc").read_bytes()
    with open_archive(
        NonSeekableBytesIO(data) if streaming else io.BytesIO(data),
        streaming=streaming,
    ) as reader:
        read = {
            member.name: (member, stream.read() if stream is not None else None)
            for member, stream in reader.stream_members()
        }
    directory, data = read["d/"]
    assert data == b"hidden data!"
    assert directory.hashes == {}
    codes = {d.code for d in directory.diagnostics}
    assert DiagnosticCode.MEMBER_DIRECTORY_DATA_IGNORED in codes
    assert DiagnosticCode.DIGEST_UNVERIFIABLE in codes
