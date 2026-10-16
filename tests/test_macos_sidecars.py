"""macOS AppleDouble sidecars are paired with the members they describe, by name."""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from archivey import open_archive
from archivey.types import EXTRA_MACOS_RESOURCE_OF
from tests.streams_util import NonSeekableBytesIO


def _zip(names: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, b"" if name.endswith("/") else b"x")
    return buffer.getvalue()


def _tar(names: list[str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name in names:
            info = tarfile.TarInfo(name.rstrip("/"))
            if name.endswith("/"):
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
    return buffer.getvalue()


def _pairs(members) -> dict[str, str]:
    return {
        m.name: m.extra[EXTRA_MACOS_RESOURCE_OF]
        for m in members
        if EXTRA_MACOS_RESOURCE_OF in m.extra
    }


@pytest.mark.parametrize("pipe", [False, True])
def test_a_finder_zip_pairs_each_sidecar_with_its_member(pipe: bool) -> None:
    # Finder's layout: the folder, its sidecar after it, files with sidecars after
    # them, and no __MACOSX directory entries.
    data = _zip(
        [
            "weird/",
            "__MACOSX/._weird",
            "weird/file.txt",
            "__MACOSX/weird/._file.txt",
            "weird/sub/",
            "__MACOSX/weird/._sub",
            "__MACOSX/weird/._gone.txt",
        ]
    )
    source = NonSeekableBytesIO(data) if pipe else io.BytesIO(data)
    with open_archive(source, streaming=pipe) as reader:
        members = [m for m, _ in reader.stream_members()] if pipe else reader.members()
    assert _pairs(members) == {
        "__MACOSX/._weird": "weird/",
        "__MACOSX/weird/._file.txt": "weird/file.txt",
        "__MACOSX/weird/._sub": "weird/sub/",
    }


def test_a_zip_sidecar_listed_before_its_member_is_paired() -> None:
    data = _zip(["__MACOSX/._a.txt", "a.txt", "__MACOSX/"])
    with open_archive(io.BytesIO(data)) as reader:
        assert _pairs(reader.members()) == {"__MACOSX/._a.txt": "a.txt"}


def test_names_that_only_look_like_sidecars_are_left_alone() -> None:
    data = _zip(["._", "dir/", "._dir/", "a.txt", "__MACOSX/a.txt", "x._a.txt"])
    with open_archive(io.BytesIO(data)) as reader:
        assert _pairs(reader.members()) == {}


@pytest.mark.parametrize("streaming", [False, True])
def test_a_macos_tar_pairs_the_sidecar_written_before_its_file(
    streaming: bool,
) -> None:
    # macOS tar writes ._name next to the file, before it.
    data = _tar(["dir/", "dir/._a.txt", "dir/a.txt", "._dir", "._lonely"])
    with open_archive(io.BytesIO(data), streaming=streaming) as reader:
        if streaming:
            members = [m for m, _ in reader.stream_members()]
        else:
            members = reader.members()
    assert _pairs(members) == {"dir/._a.txt": "dir/a.txt", "._dir": "dir/"}
