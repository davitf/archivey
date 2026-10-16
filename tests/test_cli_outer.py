"""``extract --outer`` and ``test --outer``: the compression layer alone, as gunzip does."""

from __future__ import annotations

import gzip
import io
import lzma
import os
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path

import pytest

from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK, EXIT_USAGE
from archivey.cli.main import main


def _tar_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("a.txt")
        info.size = 6
        tf.addfile(info, io.BytesIO(b"hello\n"))
    return buf.getvalue()


def _run(argv: list[str]) -> tuple[int, str]:
    err = io.StringIO()
    code = main([*argv, "--hide-progress"], out=io.StringIO(), err=err)
    return code, err.getvalue()


@pytest.mark.parametrize(
    ("name", "compress", "written"),
    [
        ("data.tar.gz", gzip.compress, "data.tar"),
        ("data.tgz", gzip.compress, "data.tar"),
        ("data.txz", lzma.compress, "data.tar"),
        ("data.tar.xz", lzma.compress, "data.tar"),
    ],
)
def test_extract_outer_writes_the_tar_inside(
    tmp_path: Path, name: str, compress: Callable[[bytes], bytes], written: str
) -> None:
    src = tmp_path / name
    src.write_bytes(compress(_tar_bytes()))
    dest = tmp_path / "out"
    code, err = _run(["extract", str(src), "--outer", "-d", str(dest)])
    assert code == EXIT_OK, err
    assert sorted(os.listdir(dest)) == [written]
    assert (dest / written).read_bytes() == _tar_bytes()


def test_extract_outer_on_a_single_compressed_file_matches_plain_extract(
    tmp_path: Path,
) -> None:
    src = tmp_path / "notes.txt.gz"
    src.write_bytes(gzip.compress(b"notes\n"))
    for flags, dest in (([], "plain"), (["--outer"], "outer")):
        code, err = _run(["extract", str(src), *flags, "-d", str(tmp_path / dest)])
        assert code == EXIT_OK, err
        assert (tmp_path / dest / "notes.txt").read_bytes() == b"notes\n"


def test_extract_outer_keeps_the_overwrite_policy(tmp_path: Path) -> None:
    src = tmp_path / "data.tar.gz"
    src.write_bytes(gzip.compress(_tar_bytes()))
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "data.tar").write_bytes(b"mine")
    code, err = _run(["extract", str(src), "--outer", "-d", str(dest)])
    assert code == EXIT_OK, err
    assert (dest / "data.tar").read_bytes() == b"mine"
    assert sorted(os.listdir(dest)) == ["data (1).tar", "data.tar"]


def test_test_outer_reads_the_whole_decompressed_stream(tmp_path: Path) -> None:
    src = tmp_path / "data.tar.gz"
    src.write_bytes(gzip.compress(_tar_bytes()))
    code, err = _run(["test", str(src), "--outer"])
    assert code == EXIT_OK, err
    assert "1 OK, 0 failed" in err


def test_test_outer_fails_on_a_bad_trailer(tmp_path: Path) -> None:
    data = bytearray(gzip.compress(_tar_bytes()))
    data[-8] ^= 0xFF  # the CRC-32 of the decompressed bytes
    src = tmp_path / "data.tar.gz"
    src.write_bytes(bytes(data))
    code, err = _run(["test", str(src), "--outer"])
    assert code == EXIT_FAIL
    assert "FAIL data.tar" in err


@pytest.mark.parametrize("verb", ["extract", "test"])
def test_outer_on_a_container_says_to_leave_the_flag_out(
    tmp_path: Path, verb: str
) -> None:
    src = tmp_path / "plain.tar"
    src.write_bytes(_tar_bytes())
    code, err = _run([verb, str(src), "--outer"])
    assert code == EXIT_FAIL
    assert "is a tar archive, which has no compression layer" in err
    assert f"leave out --outer to {verb} its members" in err


@pytest.mark.parametrize("verb", ["extract", "test"])
def test_outer_with_patterns_is_a_usage_error(tmp_path: Path, verb: str) -> None:
    src = tmp_path / "data.tar.gz"
    src.write_bytes(gzip.compress(_tar_bytes()))
    code, err = _run([verb, str(src), "--outer", "a.txt"])
    assert code == EXIT_USAGE
    assert "takes no member patterns" in err


def test_outer_on_a_directory_is_a_usage_error(tmp_path: Path) -> None:
    code, err = _run(["test", str(tmp_path), "--outer"])
    assert code == EXIT_USAGE
    assert "is a directory, which has no compression layer" in err


def test_outer_names_a_missing_path_like_the_other_verbs(tmp_path: Path) -> None:
    missing = str(tmp_path / "nope.gz")
    assert _run(["test", missing, "--outer"]) == _run(["test", missing])


@pytest.mark.skipif(sys.platform == "win32", reason="no FIFOs on Windows")
def test_outer_on_a_pipe_points_at_cat(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe.gz"
    os.mkfifo(fifo)
    code, err = _run(["extract", str(fifo), "--outer", "-d", str(tmp_path / "o")])
    assert code == EXIT_USAGE
    assert "archivey cat --outer" in err
