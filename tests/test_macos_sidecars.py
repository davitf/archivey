"""macOS AppleDouble sidecars are marked with the path they describe, by name alone."""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from archivey import open_archive
from archivey.internal.naming import macos_sidecar_target
from archivey.types import EXTRA_MACOS_RESOURCE_OF, ArchiveMember, MemberType
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


def _streamed_pairs(reader) -> dict[str, str]:
    # Read the key as each member is yielded: a pass's caller sees nothing later.
    return _pairs(m for m, _ in reader.stream_members())


@pytest.mark.parametrize("pipe", [False, True])
def test_a_finder_zip_marks_each_sidecar(pipe: bool) -> None:
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
    expected = {
        "__MACOSX/._weird": "weird",
        "__MACOSX/weird/._file.txt": "weird/file.txt",
        "__MACOSX/weird/._sub": "weird/sub",
        "__MACOSX/weird/._gone.txt": "weird/gone.txt",
    }
    source = NonSeekableBytesIO(data) if pipe else io.BytesIO(data)
    with open_archive(source, streaming=pipe) as reader:
        assert _streamed_pairs(reader) == expected
        if not pipe:
            assert _pairs(reader.members()) == expected


@pytest.mark.parametrize("pipe", [False, True])
def test_a_zip_sidecar_listed_before_its_member_is_marked(pipe: bool) -> None:
    data = _zip(["__MACOSX/._a.txt", "a.txt", "__MACOSX/"])
    source = NonSeekableBytesIO(data) if pipe else io.BytesIO(data)
    with open_archive(source, streaming=pipe) as reader:
        members = [m for m, _ in reader.stream_members()]
    assert _pairs(members) == {"__MACOSX/._a.txt": "a.txt"}


def test_names_not_shaped_like_a_sidecar_get_no_key() -> None:
    data = _zip(["._", "dir/", "._dir/", "a.txt", "__MACOSX/a.txt", "x._a.txt"])
    with open_archive(io.BytesIO(data)) as reader:
        assert _pairs(reader.members()) == {}


def test_any_name_shaped_like_a_sidecar_gets_the_key() -> None:
    # The name alone decides: a ._notes written by any tool is marked.
    data = _zip(["notes", "._notes"])
    with open_archive(io.BytesIO(data)) as reader:
        assert _pairs(reader.members()) == {"._notes": "notes"}


@pytest.mark.parametrize("streaming", [False, True])
def test_a_macos_tar_marks_the_sidecar_written_before_its_file(
    streaming: bool,
) -> None:
    # macOS tar writes ._name next to the file, before it.
    data = _tar(
        ["dir/", "dir/._a.txt", "dir/a.txt", "._dir", "._lonely", "__MACOSX/._b", "b"]
    )
    # A tarred Finder extract: the __MACOSX/ prefix is dropped in a TAR too.
    expected = {
        "dir/._a.txt": "dir/a.txt",
        "._dir": "dir",
        "._lonely": "lonely",
        "__MACOSX/._b": "b",
    }
    with open_archive(io.BytesIO(data), streaming=streaming) as reader:
        assert _streamed_pairs(reader) == expected
        if not streaming:
            assert _pairs(reader.members()) == expected


@pytest.mark.parametrize("streaming", [False, True])
def test_an_extraction_filter_leaves_out_a_tar_sidecar_written_first(
    tmp_path, streaming: bool
) -> None:
    data = _tar(["dir/", "dir/._a.txt", "dir/a.txt"])

    def skip_sidecars(member: ArchiveMember) -> ArchiveMember | None:
        return None if EXTRA_MACOS_RESOURCE_OF in member.extra else member

    with open_archive(io.BytesIO(data), streaming=streaming) as reader:
        reader.extract_all(tmp_path, filter=skip_sidecars)
    assert sorted(p.name for p in (tmp_path / "dir").iterdir()) == ["a.txt"]


def _file(name: str) -> ArchiveMember:
    return ArchiveMember(name=name, type=MemberType.FILE)


@pytest.mark.parametrize(
    ("name", "target"),
    [
        ("._a", "a"),
        ("d/._a", "d/a"),
        ("__MACOSX/._a", "a"),
        ("__MACOSX/d/._a", "d/a"),
        ("._", None),
        ("__MACOSX/._", None),
        ("a", None),
        ("x._a", None),
        ("__MACOSX/a", None),
        ("._a/b", None),
    ],
)
def test_macos_sidecar_target(name: str, target: str | None) -> None:
    assert macos_sidecar_target(_file(name)) == target


def test_only_a_file_is_a_sidecar() -> None:
    member = ArchiveMember(name="._d/", type=MemberType.DIRECTORY)
    assert macos_sidecar_target(member) is None
