from pathlib import Path

import pytest

from archivey.archive_path import ArchivePath

TEST_ZIP = Path("tests/test_archives/basic_nonsolid__zipfile.zip")


@pytest.mark.parametrize("path_cls", [Path, ArchivePath])
def test_basic_path_compatibility(path_cls):
    p = path_cls(TEST_ZIP)
    assert str(p) == str(TEST_ZIP)


def test_iterdir_and_open():
    archive = ArchivePath(TEST_ZIP)
    names = {p.name for p in archive.iterdir()}
    assert {"file1.txt", "subdir", "empty_subdir", "implicit_subdir"} <= names

    file1 = archive / "file1.txt"
    assert file1.read_text() == "Hello, world!"


def test_direct_member_path():
    p = ArchivePath("tests/test_archives/basic_nonsolid__zipfile.zip/subdir/file2.txt")
    assert p.read_text() == "Hello, universe!"


def test_write_mode_not_allowed():
    archive = ArchivePath(TEST_ZIP) / "file1.txt"
    with pytest.raises(ValueError):
        archive.open("w")
