"""A numbered volume part that does not exist is a missing file, not an incomplete set."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.exceptions import TruncatedError


@pytest.mark.parametrize("name", ["nope.zip.003", "nope.7z.001", "nope.exe.002"])
def test_missing_numbered_part_raises_file_not_found(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    with pytest.raises(FileNotFoundError) as excinfo:
        open_archive(path)
    assert excinfo.value.filename == str(path)
    assert "found part" not in str(excinfo.value)


def test_missing_numbered_part_in_a_one_item_list(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        open_archive([tmp_path / "nope.zip.003"])


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_unstatable_numbered_part_keeps_the_os_error(tmp_path: Path) -> None:
    """A part that is there but cannot be stat'ed is not reported as missing."""
    path = tmp_path / "loop.zip.001"
    path.symlink_to(path.name)  # ELOOP, not ENOENT
    with pytest.raises(OSError) as excinfo:
        open_archive(path)
    assert not isinstance(excinfo.value, FileNotFoundError)
    assert excinfo.value.filename == str(path)


def test_existing_lone_numbered_part_still_names_the_missing_parts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "set.zip.003"
    path.write_bytes(b"\x00" * 64)
    with pytest.raises(TruncatedError, match="found part 3 only"):
        open_archive(path)
