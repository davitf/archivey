from pathlib import Path

from archivey import open_archive, open_archive_writer
from archivey.types import MemberType


def _write_sample(writer):
    with writer.open("file.txt") as f:
        f.write(b"foo")
        f.write(b"bar")
    writer.add("dir/", MemberType.DIR)
    writer.add("link.txt", MemberType.SYMLINK, link_target="file.txt")


def _check_contents(path: Path):
    with open_archive(path) as archive:
        members = {m.filename: m for m in archive.get_members()}
        assert set(members) == {"file.txt", "dir/", "link.txt"}
        with archive.open(members["file.txt"]) as f:
            assert f.read() == b"foobar"
        assert members["link.txt"].link_target == "file.txt"
        assert members["dir/"].type == MemberType.DIR


def test_zip_writer(tmp_path: Path):
    archive_path = tmp_path / "out.zip"
    with open_archive_writer(archive_path) as writer:
        _write_sample(writer)
    _check_contents(archive_path)


def test_tar_writer(tmp_path: Path):
    archive_path = tmp_path / "out.tar"
    with open_archive_writer(archive_path) as writer:
        _write_sample(writer)
    _check_contents(archive_path)
