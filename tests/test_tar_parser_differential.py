"""The native TAR walker against stdlib ``tarfile``, field by field and byte by byte.

``tarfile`` is the oracle here, as ``py7zr`` and ``rarfile`` are for 7z and RAR: on a
well-formed archive both must list the same members with the same data. The archives
come from the test corpus (written by ``tarfile``) and from GNU tar, which writes the
shapes ``tarfile`` does not (old GNU sparse with real holes, the three PAX sparse
versions, ``oldgnu``). GNU tar is the format's official tool, so those cases skip where
it is not installed.

Known differences, where the native walker follows GNU tar on purpose, are covered in
``test_tar_parser.py`` with crafted archives, not here.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import BinaryIO

import pytest

from archivey.internal.backends.tar_parser import (
    SparseFormat,
    TarEntry,
    TarWalker,
    read_sparse_map_1_0,
    validate_sparse_map,
)
from tests.sample_archives import CORPUS, corpus_archive_path

_REGULAR = frozenset((b"0", b"\x00", b"7", b"S"))


def _no_limit(nbytes: int, what: str) -> None:
    pass


def _member_data(stream: BinaryIO, entry: TarEntry) -> bytes:
    """The member's logical bytes, holes as zeros, read from a seekable stream."""
    stream.seek(entry.data_offset)
    sparse = entry.sparse
    stored = entry.stored_size
    if entry.sparse_format is SparseFormat.PAX_1_0:
        sparse, used = read_sparse_map_1_0(stream.read, stored, "m", _no_limit)
        stored -= used
    if sparse is None:
        return stream.read(stored)
    assert validate_sparse_map(sparse, entry.size, stored, "m") is None
    out = bytearray(entry.size)
    for offset, length in zip(sparse.offsets, sparse.lengths, strict=True):
        out[offset : offset + length] = stream.read(length)
    return bytes(out)


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def _native_listing(path: Path) -> list[tuple[object, ...]]:
    rows = []
    with path.open("rb") as stream:
        walker = TarWalker(stream, seekable=True)
        while isinstance(entry := walker.next_entry(), TarEntry):
            typeflag = entry.typeflag
            name = _text(entry.name)
            if typeflag == b"5" or entry.old_style_directory:
                typeflag, name = b"5", name.rstrip("/")
            uname = entry.uname_pax.value if entry.uname_pax else entry.uname
            gname = entry.gname_pax.value if entry.gname_pax else entry.gname
            data = _member_data(stream, entry) if typeflag in _REGULAR else None
            link = _text(entry.linkname) if entry.linkname is not None else None
            rows.append(
                (name, typeflag, entry.size, link, entry.uid, entry.gid)
                + (_text(uname), _text(gname), entry.header.mode & 0o7777)
                + (entry.sparse_format is not None, data)
            )
        assert walker.end is not None and walker.end.kind == "zero_block"
    return rows


def _tarfile_listing(path: Path) -> list[tuple[object, ...]]:
    rows = []
    with tarfile.open(path, encoding="utf-8", errors="surrogateescape") as tar:
        for info in tar:
            data = None
            if info.isreg():
                extracted = tar.extractfile(info)
                assert extracted is not None
                data = extracted.read()
            link = info.linkname if info.issym() or info.islnk() else None
            rows.append(
                (info.name, info.type, info.size, link, info.uid, info.gid)
                + (info.uname, info.gname, info.mode & 0o7777)
                + (info.issparse(), data)
            )
    return rows


def _forward_only_matches(path: Path) -> None:
    """A forward-only walk reading each member through ``open_data`` gives the same
    stored bytes as the seekable walk."""
    data = path.read_bytes()
    seekable = TarWalker(io.BytesIO(data), seekable=True)
    forward = TarWalker(io.BytesIO(data), seekable=False)
    while isinstance(a := seekable.next_entry(), TarEntry):
        b = forward.next_entry()
        assert isinstance(b, TarEntry)
        assert (a.name, a.data_offset, a.stored_size) == (
            b.name,
            b.data_offset,
            b.stored_size,
        )
        assert (
            forward.open_data(b).read()
            == data[a.data_offset : a.data_offset + a.stored_size]
        )
    assert forward.next_entry() == seekable.end


_TAR_ENTRIES = [entry for entry in CORPUS if "tar" in entry.formats]


@pytest.mark.parametrize("entry", _TAR_ENTRIES, ids=lambda e: e.id)
def test_corpus_listing_matches_tarfile(entry, tmp_path: Path) -> None:
    path = corpus_archive_path(entry, "tar", tmp_path)
    assert _native_listing(path) == _tarfile_listing(path)
    _forward_only_matches(path)


def _tarfile_archive(path: Path, fmt: int) -> Path:
    def add(
        tar: tarfile.TarFile, name: str, data: bytes = b"", **attrs: object
    ) -> None:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        for key, value in attrs.items():
            setattr(info, key, value)
        tar.addfile(info, io.BytesIO(data) if data else None)

    with tarfile.open(path, "w", format=fmt, encoding="utf-8") as tar:
        add(tar, "plain.txt", b"hello\n", uname="user", gname="group", uid=1000)
        add(tar, "dir/", type=tarfile.DIRTYPE, mode=0o755)
        add(tar, "dir/" + "n" * 90 + "/" + "m" * 60, b"deep")
        add(tar, "link", type=tarfile.SYMTYPE, linkname="plain.txt")
        add(tar, "hard", type=tarfile.LNKTYPE, linkname="plain.txt")
        add(tar, "big", os.urandom(70_000))
        add(tar, "empty")
        add(tar, "fifo", type=tarfile.FIFOTYPE)
        add(tar, "dev", type=tarfile.CHRTYPE, devmajor=1, devminor=3)
        if fmt != tarfile.USTAR_FORMAT:
            add(tar, "long/" + "x" * 200, b"long name")
            add(tar, "longlink", type=tarfile.SYMTYPE, linkname="t" * 150)
            add(tar, "café-日本.txt", b"utf8", uid=2**22)
    return path


@pytest.mark.parametrize(
    "fmt",
    [tarfile.USTAR_FORMAT, tarfile.GNU_FORMAT, tarfile.PAX_FORMAT],
    ids=["ustar", "gnu", "pax"],
)
def test_tarfile_formats_match(fmt: int, tmp_path: Path) -> None:
    path = _tarfile_archive(tmp_path / "t.tar", fmt)
    assert _native_listing(path) == _tarfile_listing(path)
    _forward_only_matches(path)


def _gnu_tar() -> str | None:
    tar = shutil.which("tar")
    if tar is None:
        return None
    try:
        version = subprocess.run(
            [tar, "--version"], capture_output=True, check=True, text=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return tar if "GNU tar" in version else None


_GNU_TAR = _gnu_tar()
needs_gnu_tar = pytest.mark.skipif(_GNU_TAR is None, reason="needs GNU tar")


def _sparse_tree(root: Path) -> Path:
    root.mkdir()
    (root / "a.txt").write_bytes(b"hello\n")
    with (root / "holes").open("wb") as f:  # data, a hole, data, ends in data
        f.seek(100_000)
        f.write(b"x" * 700)
        f.seek(300_000)
        f.write(b"y" * 5)
    with (root / "trailing-hole").open("wb") as f:  # starts with data, ends in a hole
        f.write(b"start")
        f.seek(70_000)
        f.write(b"z" * 3)
        f.truncate(90_000)
    with (root / "many-chunks").open("wb") as f:  # more chunks than the header holds
        for i in range(40):
            f.seek(i * 8192)
            f.write(b"c" * 10)
        f.truncate(41 * 8192)
    deep = root / ("d" * 120) / ("e" * 110)
    deep.parent.mkdir()
    deep.write_bytes(b"deep\n")
    (root / "sym").symlink_to("a.txt")
    os.link(root / "a.txt", root / "hard")
    return root


@needs_gnu_tar
@pytest.mark.parametrize(
    "options",
    [
        ["--format=gnu"],
        ["--format=oldgnu"],
        ["--format=posix"],
        ["--format=pax", "--sparse-version=0.0"],
        ["--format=pax", "--sparse-version=0.1"],
        ["--format=pax", "--sparse-version=1.0"],
    ],
    ids=[
        "gnu",
        "oldgnu",
        "posix",
        "pax-sparse-0.0",
        "pax-sparse-0.1",
        "pax-sparse-1.0",
    ],
)
def test_gnu_tar_archives_match(options: list[str], tmp_path: Path) -> None:
    assert _GNU_TAR is not None
    tree = _sparse_tree(tmp_path / "tree")
    path = tmp_path / "t.tar"
    subprocess.run(
        [_GNU_TAR, *options, "--sparse", "-cf", str(path), "-C", str(tree), "."],
        check=True,
        capture_output=True,
    )
    native = _native_listing(path)
    assert native == _tarfile_listing(path)
    assert sum(1 for row in native if row[-2]) == 3  # the three sparse files
    _forward_only_matches(path)
