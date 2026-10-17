"""A ZIP's single root that meets an entry already in the cwd: same rules as the hoist.

A ZIP has an index, so with no ``-d`` extract used to write its single root straight
into the cwd and let ``--overwrite`` settle a clash there: ``replace`` put the
archive's folder over the operator's file. It now goes through the wrapper and the
move up, which never deletes the operator's data.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest

from archivey.cli.exit_codes import EXIT_OK
from archivey.cli.main import main


def _extract(src: Path, *flags: str) -> tuple[int, str]:
    err = io.StringIO()
    code = main(
        ["extract", str(src), *flags, "--hide-progress"], out=io.StringIO(), err=err
    )
    return code, err.getvalue()


@pytest.fixture
def one_zip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    src = tmp_path / "one.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("top/f", b"data\n")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return src


@pytest.mark.parametrize("policy", ["replace", "skip", "error"])
def test_a_folder_root_never_replaces_the_operator_s_file(
    one_zip: Path, policy: str
) -> None:
    Path("top").write_bytes(b"mine\n")
    code, err = _extract(one_zip, "--overwrite", policy)
    assert code == EXIT_OK, err
    assert Path("top").read_bytes() == b"mine\n"
    assert Path("one/top/f").read_bytes() == b"data\n"
    assert "kept in one/: top already exists" in err


def test_rename_still_moves_the_root_up_beside_the_file(one_zip: Path) -> None:
    Path("top").write_bytes(b"mine\n")
    code, err = _extract(one_zip)
    assert code == EXIT_OK, err
    assert Path("top").read_bytes() == b"mine\n"
    assert Path("top (1)/f").read_bytes() == b"data\n"
    assert sorted(os.listdir()) == ["top", "top (1)"]


def test_a_folder_root_still_merges_into_the_operator_s_folder(one_zip: Path) -> None:
    Path("top").mkdir()
    Path("top/mine.txt").write_bytes(b"mine\n")
    code, err = _extract(one_zip, "--overwrite", "replace")
    assert code == EXIT_OK, err
    assert sorted(os.listdir("top")) == ["f", "mine.txt"]
    assert sorted(os.listdir()) == ["top"]


def test_dry_run_predicts_the_same_kept_line(one_zip: Path) -> None:
    Path("top").write_bytes(b"mine\n")
    code, err = _extract(one_zip, "--overwrite", "replace", "--dry-run")
    assert code == EXIT_OK, err
    assert "top already exists" in err
    assert sorted(os.listdir()) == ["top"]
